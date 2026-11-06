import sqlite3
import random
import math
from io import BytesIO
from datetime import datetime

import pandas as pd
import streamlit as st

# ============================================================
# CONFIGURAÇÃO
# ============================================================
st.set_page_config(
    page_title="Formação de Times | Keirsey",
    page_icon="👥",
    layout="wide"
)

DB_PATH = "times_keirsey.db"

DICHOTOMIES = {
    "Q1": {
        "dimension": "Energia",
        "a": ("E", "Extroversão", "Mais falante, expansivo, social e expressivo. Costuma agir e interagir com o ambiente."),
        "b": ("I", "Introversão", "Mais reservado, observador e introspectivo. Costuma refletir antes de agir.")
    },
    "Q2": {
        "dimension": "Percepção",
        "a": ("S", "Sensação", "Prático, atento aos detalhes, aos fatos, à experiência e ao que é concreto."),
        "b": ("N", "Intuição", "Voltado a ideias, possibilidades, imaginação, visão global e possibilidades futuras.")
    },
    "Q3": {
        "dimension": "Decisão",
        "a": ("T", "Pensamento", "Prioriza análise lógica, critérios, objetividade e consistência nas decisões."),
        "b": ("F", "Sentimento", "Considera valores, impactos humanos, harmonia e relações interpessoais nas decisões.")
    },
    "Q4": {
        "dimension": "Organização",
        "a": ("P", "Percepção", "Prefere flexibilidade, espontaneidade, adaptação e abertura a novas possibilidades."),
        "b": ("J", "Julgamento", "Prefere planejamento, organização, estrutura, decisões e objetivos definidos.")
    }
}

TEMPERAMENTS = {
    "SJ": "Guardião",
    "SP": "Artesão",
    "NF": "Idealista",
    "NT": "Racional"
}

PROFILE_ROLES = {
    "Guardião": "Gerência de projetos, testes e gestão",
    "Artesão": "Investigação, implantação, DevOps e atividades práticas",
    "Idealista": "Análise de requisitos, criatividade e comunicação",
    "Racional": "Arquitetura de software, programação, dados e inovação"
}


# ============================================================
# BANCO DE DADOS
# ============================================================
def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                gender TEXT NOT NULL,
                q1 TEXT NOT NULL,
                q2 TEXT NOT NULL,
                q3 TEXT NOT NULL,
                q4 TEXT NOT NULL,
                profile TEXT NOT NULL,
                temperament TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_number INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS team_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                response_id INTEGER NOT NULL,
                FOREIGN KEY(team_id) REFERENCES teams(id),
                FOREIGN KEY(response_id) REFERENCES responses(id)
            )
        """)
        conn.commit()


def clear_db():
    """Limpa todos os dados salvos no banco de dados."""
    with get_connection() as conn:
        conn.execute("DELETE FROM team_members")
        conn.execute("DELETE FROM teams")
        conn.execute("DELETE FROM responses")
        # Reinicia os contadores dos IDs autoincrementados
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('team_members', 'teams', 'responses')")
        conn.commit()


def load_responses():
    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT * FROM responses ORDER BY name COLLATE NOCASE",
            conn
        )


def save_response(name, gender, answers):
    letters = "".join(answers)
    if letters[1] == "S":
        temperament_code = "S" + letters[3]
    elif letters[1] == "N" and letters[2] == "F":
        temperament_code = "NF"
    elif letters[1] == "N" and letters[2] == "T":
        temperament_code = "NT"

    temperament = TEMPERAMENTS[temperament_code]
    now = datetime.now().isoformat(timespec="seconds")

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO responses
            (name, gender, q1, q2, q3, q4, profile, temperament, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name.strip(), gender, *answers, letters, temperament, now))
        conn.execute("DELETE FROM team_members")
        conn.execute("DELETE FROM teams")
        conn.commit()


def save_teams(assignments):
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("DELETE FROM team_members")
        conn.execute("DELETE FROM teams")
        for team_number, members in enumerate(assignments, start=1):
            cursor = conn.execute(
                "INSERT INTO teams (team_number, created_at) VALUES (?, ?)",
                (team_number, now)
            )
            team_id = cursor.lastrowid
            for member in members:
                conn.execute(
                    "INSERT INTO team_members (team_id, response_id) VALUES (?, ?)",
                    (team_id, int(member["id"]))
                )
        conn.commit()


def load_saved_teams():
    query = """
        SELECT t.team_number, r.id, r.name, r.gender, r.profile, r.temperament
        FROM teams t
        JOIN team_members tm ON tm.team_id = t.id
        JOIN responses r ON r.id = tm.response_id
        ORDER BY t.team_number, r.name COLLATE NOCASE
    """
    with get_connection() as conn:
        return pd.read_sql_query(query, conn)


# ============================================================
# ALGORITMO DE FORMAÇÃO DOS TIMES
# ============================================================
def create_balanced_teams(df, number_of_teams, attempts=300):
    if df.empty:
        return []

    n = len(df)
    k = max(1, min(int(number_of_teams), n))

    base_size = n // k
    remainder = n % k
    capacities = [base_size + (1 if i < remainder else 0) for i in range(k)]

    women_count = int((df["gender"] == "Mulher").sum())

    records = df.to_dict("records")
    best_teams = None
    best_score = float("-inf")

    for _ in range(attempts):
        teams = [[] for _ in range(k)]
        counts = [dict() for _ in range(k)]
        has_woman = [False] * k

        shuffled = records[:]
        random.shuffle(shuffled)
        shuffled.sort(
            key=lambda x: (
                x["gender"] != "Mulher",
                x["temperament"]
            )
        )

        for person in shuffled:
            available = [i for i in range(k) if len(teams[i]) < capacities[i]]
            if not available:
                break

            def placement_score(team_index):
                temperament = person["temperament"]
                score = 0

                if person["gender"] == "Mulher" and not has_woman[team_index]:
                    score += 100

                if counts[team_index].get(temperament, 0) == 0:
                    score += 30
                else:
                    score -= 12 * counts[team_index][temperament]

                if person["gender"] != "Mulher" and women_count >= k and not has_woman[team_index]:
                    score -= 3

                score -= len(teams[team_index]) * 0.1
                return score + random.random() * 0.5

            chosen = max(available, key=placement_score)
            teams[chosen].append(person)
            counts[chosen][person["temperament"]] = (
                counts[chosen].get(person["temperament"], 0) + 1
            )
            if person["gender"] == "Mulher":
                has_woman[chosen] = True

        covered_women = sum(has_woman)
        diversity = sum(len(set(p["temperament"] for p in team)) for team in teams)
        repetition_penalty = sum(
            sum(max(0, value - 1) ** 2 for value in team_counts.values())
            for team_counts in counts
        )
        sizes = [len(team) for team in teams]
        size_penalty = max(sizes) - min(sizes) if sizes else 0

        score = (
            covered_women * 1000
            + diversity * 100
            - repetition_penalty * 15
            - size_penalty * 100
        )

        if score > best_score:
            best_score = score
            best_teams = teams

    return best_teams or []


# ============================================================
# INTERFACE
# ============================================================
init_db()

st.title("👥 Formação de Times por Perfil de Keirsey")
st.caption("Questionário de preferências e composição colaborativa de equipes.")

tab_student, tab_teacher, tab_about = st.tabs(
    ["📝 Questionário", "📊 Painel do professor", "ℹ️ Sobre"]
)

# ------------------------------------------------------------
# ABA DO ESTUDANTE
# ------------------------------------------------------------
with tab_student:
    st.subheader("Questionário de perfil")
    st.write(
        "Leia as duas alternativas de cada questão e escolha aquela que "
        "mais se aproxima do seu comportamento habitual. Não existe resposta certa ou errada."
    )

    with st.form("student_form", clear_on_submit=True):
        name = st.text_input("Nome completo", max_chars=120)

        gender = st.selectbox(
            "Como você se identifica? (usado somente para auxiliar a composição dos times)",
            ["Selecione...", "Mulher", "Homem", "Pessoa não binária / outra identidade",
             "Prefiro não informar"]
        )

        st.markdown("---")
        answers = []

        for key, item in DICHOTOMIES.items():
            st.markdown(f"**{key} — {item['dimension']}**")
            option_a = item["a"]
            option_b = item["b"]

            choice = st.radio(
                f"Qual alternativa representa melhor você?",
                options=["A", "B"],
                format_func=lambda x, a=option_a, b=option_b:
                    f"{a[0]} — {a[1]}: {a[2]}" if x == "A"
                    else f"{b[0]} — {b[1]}: {b[2]}",
                key=f"choice_{key}",
                horizontal=False
            )
            answers.append(option_a[0] if choice == "A" else option_b[0])
            st.markdown("---")

        consent = st.checkbox(
            "Confirmo que minhas respostas serão utilizadas para identificar "
            "preferências de trabalho e apoiar a formação das equipes nesta atividade."
        )

        submitted = st.form_submit_button("Enviar minhas respostas", type="primary")

    if submitted:
        if not name.strip():
            st.error("Informe seu nome.")
        elif gender == "Selecione...":
            st.error("Selecione uma opção de identificação ou 'Prefiro não informar'.")
        elif not consent:
            st.error("É necessário confirmar a finalidade de uso das respostas.")
        else:
            save_response(name, gender, answers)
            profile = "".join(answers)
            if profile[1] == "S":
                temperament_code = "S" + profile[3]
            else:
                temperament_code = "N" + profile[2]
            temperament = TEMPERAMENTS[temperament_code]
            st.success(
                f"Respostas registradas! Seu perfil calculated é **{profile}** "
                f"— temperamento **{temperament}**."
            )
            st.info("Aguarde a orientação do professor para conhecer sua equipe.")


# ------------------------------------------------------------
# ABA DO PROFESSOR
# ------------------------------------------------------------
with tab_teacher:
    col_header, col_delete = st.columns([0.7, 0.3])
    with col_header:
        st.subheader("Painel de acompanhamento")
    
    with col_delete:
        # Caixas de confirmação para evitar exclusão acidental
        with st.popover("🗑️ Reiniciar / Excluir Banco"):
            st.warning("⚠️ Esta ação apagará permanentemente todas as respostas e times cadastrados!")
            if st.button("Confirmar e Excluir Tudo", type="primary", use_container_width=True):
                clear_db()
                st.success("Banco de dados reiniciado com sucesso!")
                st.rerun()

    df = load_responses()
    total = len(df)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Respostas recebidas", total)
    c2.metric("Guardião", int((df["temperament"] == "Guardião").sum()) if total else 0)
    c3.metric("Artesão", int((df["temperament"] == "Artesão").sum()) if total else 0)
    c4.metric("Idealista + Racional",
              int(df["temperament"].isin(["Idealista", "Racional"]).sum()) if total else 0)

    if total:
        women = int((df["gender"] == "Mulher").sum())
        st.caption(
            f"{women} estudante(s) identificada(s) como mulher(es). "
            f"A presença feminina poderá ser garantida em até {min(women, total)} equipe(s), "
            "dependendo da quantidade de equipes escolhida."
        )

        with st.expander("Visualizar respostas recebidas"):
            display = df[["name", "gender", "profile", "temperament", "created_at"]].copy()
            display.columns = ["Nome", "Identificação", "Perfil", "Temperamento", "Data de envio"]
            st.dataframe(display, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### Gerar equipes")

        suggested_teams = max(1, math.ceil(total / 5))
        number_of_teams = st.number_input(
            "Quantidade de equipes",
            min_value=1,
            max_value=total,
            value=min(suggested_teams, total),
            step=1,
            help="A sugestão inicial considera aproximadamente cinco estudantes por equipe. "
                 "Você pode alterar a quantidade; o tamanho será ajustado automaticamente."
        )

        expected_sizes = [total // int(number_of_teams)] * int(number_of_teams)
        for i in range(total % int(number_of_teams)):
            expected_sizes[i] += 1
        st.caption(
            "Tamanho previsto das equipes: "
            + ", ".join(str(x) for x in expected_sizes)
            + " integrante(s)."
        )

        if st.button("✨ Gerar equipes", type="primary"):
            teams = create_balanced_teams(df, int(number_of_teams))
            save_teams(teams)
            st.session_state["teams_generated"] = True
            st.success("Equipes geradas e salvas no banco de dados.")

        saved = load_saved_teams()
        if not saved.empty:
            st.markdown("---")
            st.markdown("### Composição das equipes")

            for team_number in sorted(saved["team_number"].unique()):
                team = saved[saved["team_number"] == team_number]
                with st.container(border=True):
                    st.markdown(f"**Equipe {team_number}** · {len(team)} integrante(s)")
                    cols = st.columns(2)
                    for index, (_, person) in enumerate(team.iterrows()):
                        with cols[index % 2]:
                            st.write(
                                f"**{person['name']}**  \n"
                                f"{person['profile']} · {person['temperament']}"
                                f"  \n{person['gender']}"
                            )

                    temperaments = sorted(team["temperament"].unique())
                    woman_present = (team["gender"] == "Mulher").any()
                    st.caption(
                        "Temperamentos: " + ", ".join(temperaments)
                        + (" · Presença feminina" if woman_present else " · Sem mulher identificada")
                    )

            # Exportação Excel
            output = BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                saved.to_excel(writer, index=False, sheet_name="Equipes")
                df.to_excel(writer, index=False, sheet_name="Respostas")
            output.seek(0)

            st.download_button(
                "⬇️ Exportar respostas e equipes (Excel)",
                data=output,
                file_name="formacao_times_keirsey.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            st.download_button(
                "⬇️ Exportar composição (CSV)",
                data=saved.to_csv(index=False).encode("utf-8-sig"),
                file_name="equipes_keirsey.csv",
                mime="text/csv"
            )
    else:
        st.info("Ainda não há respostas registradas. Compartilhe o formulário com os estudantes.")

# ------------------------------------------------------------
# ABA SOBRE
# ------------------------------------------------------------
with tab_about:
    st.subheader("Como funciona a classificação")
    st.markdown("""
    | Questão | Dimensão | Alternativas |
    |---|---|---|
    | Q1 | Energia | E / I |
    | Q2 | Percepção | S / N |
    | Q3 | Decisão | T / F |
    | Q4 | Organização | P / J |

    **Temperamentos de Keirsey**

    - **Guardião:** SJ
    - **Artesão:** SP
    - **Idealista:** NF
    - **Racional:** NT

    A aplicação usa as dimensões S/N, T/F e P/J para identificar o temperamento.
    A letra E/I completa o código de quatro letras, mas não altera o temperamento.

    **Critérios de formação**

    1. Equipes com tamanhos que diferem no máximo em um integrante.
    2. Diversidade de temperamentos priorizada.
    3. Presença de pelo menos uma mulher por equipe sempre que o número de mulheres
       respondentes for igual ou superior ao número de equipes.
    4. Quando a condição não for possível, a aplicação distribui as mulheres entre
       o maior número possível de equipes e informa a limitação.

    **Importante:** este questionário é uma atividade exploratória de preferências.
    Não constitui avaliação psicológica nem mede competência técnica.
    """)

    st.warning(
        "Privacidade: antes de publicar a aplicação, informe aos estudantes a finalidade "
        "da coleta, limite o acesso do professor aos dados e defina prazo de retenção e "
        "eliminação. Em implantação institucional, valide o tratamento dos dados com "
        "as orientações de proteção de dados da instituição."
    )