"""Admin › Supervision : activité, recherches (file, blocages, échecs), santé du système, alertes, audit."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from getiajob.config import get_settings
from getiajob.services import supervision as sv
from getiajob.services.alerting import SEVERITY_LABELS, run_monitor
from getiajob.web.state import get_runner, require_admin
from getiajob.web.views.admin_format import STATUS_COLORS, duration, local, pivot

PERIODS = {"7 jours": 7, "30 jours": 30, "90 jours": 90}


def render() -> None:
    user = require_admin()
    st.markdown("## Supervision")
    _alert_banner()
    tabs = st.tabs(["Activité", "Recherches", "Santé & alertes", "Journal d'audit"])
    with tabs[0]:
        _activity()
    with tabs[1]:
        _searches(user.id)
    with tabs[2]:
        _health()
    with tabs[3]:
        _audit()


def _alert_banner() -> None:
    open_alerts = [a for a in sv.alerts() if a.resolved_at is None]
    if not open_alerts:
        return
    critical = any(a.severity == "critical" for a in open_alerts)
    lines = "\n".join(
        f"- **{SEVERITY_LABELS.get(a.severity, a.severity)}** — {a.message}" for a in open_alerts
    )
    (st.error if critical else st.warning)(
        f"{len(open_alerts)} alerte(s) ouverte(s) :\n{lines}", icon=":material/notifications_active:"
    )


# --------------------------------------------------------------------------- #


def _activity() -> None:
    label = st.segmented_control("Période", list(PERIODS), default="30 jours", key="sv_period")
    days = PERIODS.get(label or "30 jours", 30)
    a = sv.activity(days)
    c = st.columns(4)
    c[0].metric("Recherches", a.searches)
    c[1].metric("Taux d'échec", f"{a.failure_rate:.0%}" if a.failure_rate is not None else "–")
    c[2].metric("Durée moyenne", duration(a.avg_seconds))
    c[3].metric("Utilisateurs actifs (7 j / 30 j)", f"{a.active_7} / {a.active_30}")
    c = st.columns(4)
    c[0].metric("Comptes", a.users, delta=f"+{a.new_users}" if a.new_users else None, delta_color="off")
    c[1].metric("CV déposés", a.cvs, delta=f"+{a.new_cvs}" if a.new_cvs else None, delta_color="off")
    c[2].metric("Analyses de CV en échec", len(a.cv_failures))
    c[3].metric("Recherches en échec", a.failed)

    st.markdown("#### Recherches par jour")
    if a.per_day:
        order = [s for s in ("Terminée", "Échec", "En cours") if any(r["statut"] == s for r in a.per_day)]
        st.bar_chart(
            pivot(a.per_day, "jour", "statut", "n", order),
            color=[STATUS_COLORS[s] for s in order],
            x_label="",
            y_label="Recherches",
        )
    else:
        st.caption("Aucune recherche sur la période.")

    if a.cv_failures:
        st.markdown("#### Analyses de CV en échec")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "CV": r["file_name"],
                        "Utilisateur": r["email"],
                        "Date": local(r["analysis_started_at"]),
                        "Erreur": r["analysis_error"],
                    }
                    for r in a.cv_failures
                ]
            ),
            hide_index=True,
            width="stretch",
        )


# --------------------------------------------------------------------------- #


def _searches(actor_id: int) -> None:
    settings = get_settings()
    active = sv.active_searches()
    running = [r for r in active if r.status == "running"]
    pending = [r for r in active if r.status == "pending"]
    stuck = [r for r in running if r.is_stuck(settings.stuck_search_minutes)]
    c = st.columns(3)
    c[0].metric("En cours", len(running))
    c[1].metric("En file d'attente", len(pending))
    c[2].metric(f"Bloquées (> {settings.stuck_search_minutes} min)", len(stuck))

    st.markdown("#### File de traitement")
    if not active:
        st.caption("Aucune recherche en cours ni en attente.")
    for r in active:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            state = "En cours" if r.status == "running" else "En attente"
            badge = (
                ":red-badge[Bloquée]"
                if r.is_stuck(settings.stuck_search_minutes)
                else f":blue-badge[{state}]"
            )
            step = f" · étape `{r.step}`" if r.step else ""
            c1.markdown(
                f"{badge} **#{r.id} {r.name}** — {r.email or 'compte supprimé'}  \n"
                f"Créée le {local(r.created_at)} · depuis {r.age_minutes:.0f} min{step}"
            )
            if c2.button("Arrêter", key=f"sv-cancel-{r.id}", icon=":material/stop_circle:"):
                if sv.cancel_search(actor_id, r.id):
                    st.toast(f"Recherche #{r.id} arrêtée", icon=":material/check_circle:")
                st.rerun()
    if running:
        st.caption(
            "« Arrêter » marque la recherche en échec. Un traitement déjà en cours se termine en "
            "arrière-plan sans modifier la recherche ; une recherche en attente ne sera pas lancée."
        )

    st.markdown("#### Derniers échecs")
    query = st.text_input("Filtrer par utilisateur", placeholder="email…", key="sv_fail_q")
    failed = sv.failed_searches(30, query or None)
    if not failed:
        st.caption("Aucun échec.")
    for r in failed:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            c1.markdown(
                f"**#{r.id} {r.name}** — {r.email or 'compte supprimé'} · "
                f"{local(r.finished_at or r.created_at)}"
            )
            c1.code(r.error or "Erreur inconnue", language=None, wrap_lines=True)
            if c2.button("Relancer", key=f"sv-retry-{r.id}", icon=":material/replay:"):
                if sv.retry_search(actor_id, r.id):
                    get_runner().submit_search(r.id)
                    st.toast(f"Recherche #{r.id} relancée", icon=":material/check_circle:")
                st.rerun()


# --------------------------------------------------------------------------- #


def _check_line(check: sv.Check) -> None:
    icon = {True: ":green-badge[OK]", False: ":red-badge[Problème]", None: ":gray-badge[–]"}[check.ok]
    timing = f" · {check.seconds * 1000:.0f} ms" if check.seconds is not None else ""
    st.markdown(f"{icon} **{check.label}** — {check.detail}{timing}")


def _event_check(label: str, kind: str, empty: str) -> sv.Check:
    ok_event, last = sv.last_event(kind), sv.last_event(kind, ok=None)
    if last is None:
        return sv.Check(label, None, empty)
    detail = f"dernière réussite : {local(ok_event.created_at)}" if ok_event else "jamais réussie"
    if not last.ok:
        detail += f" · dernier essai en échec ({local(last.created_at)}) : {last.detail}"
    return sv.Check(label, last.ok, detail)


def _health() -> None:
    settings = get_settings()
    st.markdown("#### État du système")
    db, tables = sv.check_database()
    with st.container(border=True):
        _check_line(db)
        _check_line(sv.check_ollama())
        _check_line(_event_check("Relève des alertes e-mail", "mail_sync", "jamais lancée"))
        _check_line(
            _event_check(
                "Sauvegarde de la base",
                "backup",
                "aucune sauvegarde enregistrée (scripts/backup-db.sh)",
            )
        )
        if st.session_state.get("sv_mailbox"):
            _check_line(st.session_state["sv_mailbox"])
        st.caption(
            f"Version {sv.app_version()} · serveur démarré le {local(sv.STARTED_AT)} · "
            f"modèle `{settings.llm_model}` · embeddings `{settings.embedding_model}`"
        )
    if st.button("Tester la connexion à la boîte mail", icon=":material/mail:"):
        with st.spinner("Connexion IMAP…"):
            st.session_state["sv_mailbox"] = sv.check_mailbox()
        st.rerun()

    if tables:
        with st.expander("Tables les plus volumineuses"):
            st.dataframe(pd.DataFrame(tables).drop(columns="octets"), hide_index=True, width="stretch")

    st.markdown("#### Alertes")
    st.caption(
        "Les alertes sont calculées par `getiajob monitor` (tâche cron `scripts/cron-monitor.sh`) et "
        "envoyées par e-mail "
        + (f"à {settings.alert_emails}" if settings.alert_emails else "aux comptes administrateurs")
        + ("." if settings.alerts_enabled else " — notifications désactivées (ALERTS_ENABLED=false).")
    )
    rows = sv.alerts()
    if not rows:
        st.caption("Aucune alerte sur les 7 derniers jours.")
    for a in rows:
        color = "gray" if a.resolved_at else ("red" if a.severity == "critical" else "orange")
        status = f"résolue le {local(a.resolved_at)}" if a.resolved_at else "ouverte"
        notified = f" · notifiée le {local(a.notified_at)}" if a.notified_at else ""
        st.markdown(
            f":{color}-badge[{SEVERITY_LABELS.get(a.severity, a.severity)}] {a.message}  \n"
            f"<small>`{a.key}` · depuis le {local(a.opened_at)} · {status}{notified}</small>",
            unsafe_allow_html=True,
        )
    with st.container(horizontal=True, gap="small"):
        check = st.button(
            "Vérifier maintenant", icon=":material/fact_check:", help="Sans rien enregistrer ni envoyer"
        )
        notify = st.button(
            "Vérifier et notifier",
            icon=":material/notifications:",
            help="Met à jour les alertes et envoie l'e-mail s'il y a du nouveau",
        )
    if check or notify:
        with st.spinner("Vérification…"):
            report = run_monitor(dry_run=check)
        if not report.findings:
            st.success("Aucun problème détecté.", icon=":material/check_circle:")
        for f in report.findings:
            st.markdown(f"- **{SEVERITY_LABELS.get(f.severity, f.severity)}** — {f.message}")
        if notify:
            if report.sent:
                st.toast(f"Notification envoyée à {len(report.recipients)} destinataire(s)")
            elif report.send_error:
                st.error(f"Notification non envoyée : {report.send_error}")
            elif not report.plan.to_notify:
                st.caption("Rien de nouveau à notifier.")


# --------------------------------------------------------------------------- #


def _audit() -> None:
    actions = sv.audit_actions()
    choice = st.selectbox(
        "Action",
        [None, *actions],
        format_func=lambda a: "Toutes les actions" if a is None else sv.AUDIT_LABELS.get(a, a),
        key="sv_audit_action",
    )
    rows = sv.audit_log(200, choice)
    if not rows:
        st.caption("Aucune action enregistrée.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Date": local(r.created_at),
                    "Par": r.actor,
                    "Action": sv.AUDIT_LABELS.get(r.action, r.action),
                    "Cible": r.target,
                    "Détail": r.detail,
                }
                for r in rows
            ]
        ),
        hide_index=True,
        width="stretch",
    )
