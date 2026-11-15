"""Admin › Sources d'annonces : état de chaque site d'offres et recherche de test."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

import streamlit as st

from getiajob.config import get_settings
from getiajob.scraping.base import ALL_SOURCES, SOURCE_INFO, SOURCE_LABELS, configured_sources
from getiajob.services.mail_alerts import STATUS_LABELS, import_counts, recent_imports, sync_mailbox
from getiajob.services.monitoring import audit
from getiajob.services.sources import MAX_TEST_RESULTS, SourceStats, SourceTest, source_stats, try_source
from getiajob.services.supervision import last_event
from getiajob.web.layout import freshness_input
from getiajob.web.state import require_admin
from getiajob.web.views.cv import ico
from getiajob.web.views.results import SOURCE_COLORS

RESULT_KEY = "admin_source_test"

CSS = """
<style>
  [class*="st-key-gj_src_"] { border-radius: 16px !important; }
  .gj-src-card { display: flex; gap: .9rem; align-items: flex-start; }
  .gj-src-logo { flex-shrink: 0; width: 2.7rem; height: 2.7rem; border-radius: 12px; display: grid;
    place-items: center; color: #fff; font-weight: 800; font-size: 1.05rem; background: var(--sc); }
  .gj-src-main { min-width: 0; flex: 1; }
  .gj-src-name { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem; font-weight: 700;
    font-size: 1.05rem; color: var(--gj-head); }
  .gj-src-name a { color: inherit; text-decoration: none; }
  .gj-src-name a:hover { text-decoration: underline; }
  .gj-src-tag { font-size: .7rem; font-weight: 700; padding: .1rem .55rem; border-radius: 999px; }
  .gj-src-tag.on { background: rgba(34, 197, 94, .14); color: #15803d; }
  .gj-src-tag.off { background: rgba(234, 88, 12, .14); color: #c2410c; }
  .gj-src-tag.kind { background: var(--gj-surface-2); color: var(--gj-muted);
    border: 1px solid var(--gj-line); }
  .gj-src-desc { font-size: .87rem; color: var(--gj-muted); margin: .25rem 0 .45rem; }
  .gj-src-meta { display: flex; flex-wrap: wrap; gap: .25rem 1.1rem; font-size: .8rem;
    color: var(--gj-muted); }
  .gj-src-meta span { display: inline-flex; align-items: center; gap: .3rem; }
  .gj-src-meta b { color: var(--gj-head); }
  .gj-src-env { margin-top: .5rem; font-size: .8rem; color: var(--gj-muted); }
  .gj-src-env code { font-size: .75rem; }
  .gj-tr { padding: .6rem 0; border-bottom: 1px solid var(--gj-line); }
  .gj-tr:last-child { border-bottom: 0; }
  .gj-tr-t { font-weight: 700; color: var(--gj-head); }
  .gj-tr-t a { color: inherit; text-decoration: none; }
  .gj-tr-t a:hover { color: var(--gj-primary); text-decoration: underline; }
  .gj-tr-m { display: flex; flex-wrap: wrap; gap: .2rem 1rem; font-size: .8rem; color: var(--gj-muted);
    margin-top: .15rem; }
  .gj-tr-m span { display: inline-flex; align-items: center; gap: .25rem; }
  .gj-tr-d { font-size: .82rem; color: var(--gj-text); margin-top: .3rem; white-space: pre-line; }
  .gj-tr-d.none { color: var(--gj-muted); font-style: italic; }
</style>
"""


def _ago(when: datetime | None) -> str:
    if when is None:
        return "jamais"
    delta = datetime.now(UTC) - when
    if delta.days <= 0:
        hours = delta.seconds // 3600
        return f"il y a {hours} h" if hours else "il y a moins d'une heure"
    return "hier" if delta.days == 1 else f"il y a {delta.days} j"


def _excerpt(text: str, size: int = 320) -> str:
    return text if len(text) <= size else text[:size].rstrip() + "…"


def _source_card(name: str, stats: SourceStats, active: bool) -> None:
    info = SOURCE_INFO[name]
    label = SOURCE_LABELS[name]
    status = (
        '<span class="gj-src-tag on">Active</span>'
        if active
        else '<span class="gj-src-tag off">Clés manquantes</span>'
    )
    meta = (
        f"<span>{ico('briefcase', 13)}<b>{stats.total}</b> offre(s) en base</span>"
        f"<span>{ico('sparkles', 13)}<b>{stats.last_7_days}</b> vue(s) ces 7 derniers jours</span>"
        f"<span>{ico('clock', 13)}Dernière collecte {_ago(stats.last_seen_at)}</span>"
    )
    env = ""
    if info.env_vars:
        names = " ".join(f"<code>{escape(v)}</code>" for v in info.env_vars)
        signup = (
            f' — clés à obtenir sur <a href="{escape(info.signup_url)}" target="_blank">'
            f"{escape(info.signup_url.removeprefix('https://'))}</a>"
            if info.signup_url and not active
            else ""
        )
        env = f'<div class="gj-src-env">Configuration (.env) : {names}{signup}</div>'
    with st.container(border=True, key=f"gj_src_{name}"):
        st.markdown(
            f'<div class="gj-src-card"><div class="gj-src-logo" style="--sc:{SOURCE_COLORS[name]}">'
            f'{escape(label[0])}</div><div class="gj-src-main"><div class="gj-src-name">'
            f'<a href="{escape(info.site)}" target="_blank" rel="noopener">{escape(label)}</a>{status}'
            f'<span class="gj-src-tag kind">{escape(info.kind)}</span></div>'
            f'<div class="gj-src-desc">{escape(info.description)}</div>'
            f'<div class="gj-src-meta">{meta}</div>{env}</div></div>',
            unsafe_allow_html=True,
        )


def _test_form(active: list[str]) -> None:
    st.markdown("### Tester une recherche")
    st.caption(
        "Interroge directement la source choisie, sans rien enregistrer en base ni lancer l'IA. "
        "Pratique pour vérifier des clés d'API, un lieu ou des mots-clés."
    )
    if not active:
        st.info("Aucune source n'est configurée.")
        return
    with st.form("source_test"):
        c1, c2, c3 = st.columns([2, 3, 2])
        source = c1.selectbox("Source", active, format_func=SOURCE_LABELS.get)
        keywords = c2.text_input("Mots-clés *", placeholder="ex. chef de projet digital")
        location = c3.text_input("Lieu", value="France", help="Ville, département, région ou « France ».")
        c1, c2 = st.columns(2)
        with c1:
            days = freshness_input("Fraîcheur des annonces", 7, key="src_test_days")
        limit = c2.slider("Nombre d'offres max", 1, MAX_TEST_RESULTS, 10)
        submitted = st.form_submit_button("Lancer le test", type="primary", icon=":material/play_arrow:")
    if submitted:
        if not keywords.strip():
            st.error("Indique des mots-clés.")
            return
        label = SOURCE_LABELS[source]
        with st.spinner(f"Interrogation de {label}…"):
            st.session_state[RESULT_KEY] = try_source(
                source, keywords.strip(), location.strip() or "France", days, limit
            )


def _test_result(res: SourceTest) -> None:
    label = SOURCE_LABELS.get(res.source, res.source)
    where = f"« {res.keywords} » @ {res.location}, {res.days} j"
    if not res.ok:
        st.error(
            f"**{label}** — échec après {res.seconds:.1f} s ({where})\n\n`{res.error}`",
            icon=":material/error:",
        )
        return
    n = len(res.postings)
    with_desc = sum(1 for p in res.postings if p.description)
    msg = f"**{label}** — {n} offre(s) en {res.seconds:.1f} s ({where})"
    if n:
        msg += f" · description présente pour {with_desc}/{n}"
        st.success(msg, icon=":material/check_circle:")
    else:
        st.warning(
            msg + " · aucune offre : élargis les mots-clés, le lieu ou la fraîcheur.", icon=":material/info:"
        )
        return
    if res.source == "linkedin" and with_desc < n:
        st.caption("LinkedIn : le détail n'est récupéré que pour les 3 premières offres pendant le test.")

    rows = []
    for p in res.postings:
        meta = []
        if p.company:
            meta.append(f"<span>{ico('briefcase', 12)}{escape(p.company)}</span>")
        if p.location:
            meta.append(f"<span>{ico('map-pin', 12)}{escape(p.location)}</span>")
        if p.posted_at:
            meta.append(f"<span>{ico('calendar', 12)}{p.posted_at:%d/%m/%Y}</span>")
        if p.salary:
            meta.append(f"<span>{ico('euro', 12)}{escape(p.salary)}</span>")
        meta += [f"<span>{escape(k)} : {escape(v)}</span>" for k, v in list(p.criteria.items())[:3]]
        meta.append(f"<span>ID <code>{escape(p.external_id)}</code></span>")
        desc = (
            f'<div class="gj-tr-d">{escape(_excerpt(p.description))}</div>'
            if p.description
            else '<div class="gj-tr-d none">Pas de description</div>'
        )
        rows.append(
            f'<div class="gj-tr"><div class="gj-tr-t"><a href="{escape(p.url, quote=True)}" target="_blank" '
            f'rel="noopener">{escape(p.title)}</a></div>'
            f'<div class="gj-tr-m">{"".join(meta)}</div>{desc}</div>'
        )
    with st.container(border=True):
        st.markdown("".join(rows), unsafe_allow_html=True)


def render() -> None:
    user = require_admin()
    st.markdown("## Sources d'annonces")
    st.html(CSS)
    active = configured_sources()
    stats = source_stats()
    st.caption(
        f"{len(active)} source(s) active(s) sur {len(ALL_SOURCES)}. Les utilisateurs choisissent parmi les "
        "sources actives dans leur fiche et à chaque recherche. Une source s'active en renseignant ses clés "
        "dans le fichier .env puis en redémarrant l'application."
    )
    for name in ALL_SOURCES:
        _source_card(name, stats[name], name in active)

    st.divider()
    _test_form([s for s in active if s != "apec"])
    if (res := st.session_state.get(RESULT_KEY)) is not None:
        _test_result(res)

    st.divider()
    _mail_section("apec" in active, user.id)
    _last_sync_caption()


def _last_sync_caption() -> None:
    last_ok, last = last_event("mail_sync"), last_event("mail_sync", ok=None)
    if last is None:
        return
    text = f"Dernière relève réussie : {last_ok.created_at.astimezone():%d/%m/%Y %H:%M}" if last_ok else ""
    if not last.ok:
        text += f" · dernière tentative en échec ({last.created_at.astimezone():%d/%m %H:%M}) : {last.detail}"
    st.caption(text.strip(" ·"))


MAIL_KEY = "admin_mail_report"
STATUS_COLORS = {"imported": "green", "unknown_sender": "orange", "unverified": "red", "not_alert": "gray"}


def _mail_section(active: bool, actor_id: int) -> None:
    settings = get_settings()
    st.markdown("### Alertes e-mail APEC")
    if not active:
        st.info(
            "Boîte non configurée : renseigner IMAP_HOST, IMAP_USER et IMAP_PASSWORD dans .env.",
            icon=":material/mail_lock:",
        )
        return
    counts = import_counts()
    st.caption(
        f"Boîte **{settings.imap_user}** ({settings.imap_host}), relevée à chaque recherche qui inclut "
        f"l'APEC. Les mails des {settings.mail_lookback_days} derniers jours sont analysés ; chaque alerte "
        "est rattachée au compte dont l'adresse l'a transférée (expéditeur authentifié). "
        + (
            "Les alertes importées sont ensuite **supprimées de la boîte** (déplacées dans sa corbeille) ; "
            "les autres mails sont conservés."
            if settings.mail_delete_processed
            else "Les mails sont conservés dans la boîte (MAIL_DELETE_PROCESSED=false)."
        )
    )
    if counts:
        st.markdown(
            " ".join(
                f":{STATUS_COLORS.get(k, 'gray')}-badge[{STATUS_LABELS.get(k, k)} : {n}]"
                for k, n in sorted(counts.items())
            )
        )
    with st.container(horizontal=True, gap="small"):
        preview = st.button(
            "Aperçu de la boîte", icon=":material/preview:", help="Analyse sans rien enregistrer ni supprimer"
        )
        run = st.button("Importer maintenant", icon=":material/move_to_inbox:", type="primary")
    if preview:
        with st.spinner("Lecture de la boîte…"):
            st.session_state[MAIL_KEY] = ("preview", sync_mailbox(dry_run=True))
    if run:
        with st.spinner("Import des nouvelles alertes…"):
            report = sync_mailbox()
            st.session_state[MAIL_KEY] = ("import", report)
            if not report.skipped:
                audit(
                    actor_id, "mail.import", f"{report.imported} alerte(s)", f"{report.new_offers} offre(s)"
                )

    if (state := st.session_state.get(MAIL_KEY)) is not None:
        mode, report = state
        if report.skipped:
            st.info("Une relève est déjà en cours (recherche ou tâche planifiée) : réessaie dans un instant.")
        elif report.error:
            st.error(f"Échec après {report.seconds:.1f} s\n\n`{report.error}`", icon=":material/error:")
        else:
            what = "nouveau(x) mail(s) analysé(s)" if mode == "import" else "mail(s) dans la période"
            extra = (
                f" · {report.new_offers} nouvelle(s) offre(s) importée(s)"
                f" · {report.deleted} mail(s) supprimé(s)"
                if mode == "import"
                else ""
            )
            st.success(
                f"{len(report.results)} {what} en {report.seconds:.1f} s{extra}", icon=":material/mail:"
            )
            for r in report.results:
                _mail_row(r)

    history = recent_imports()
    if history:
        with st.expander(f"Derniers mails traités ({len(history)})", icon=":material/history:"):
            for h in history:
                when = f"{h.received_at:%d/%m/%Y %H:%M}" if h.received_at else "—"
                who = h.user_label or h.sender or "?"
                st.markdown(
                    f":{STATUS_COLORS.get(h.status, 'gray')}-badge[{STATUS_LABELS.get(h.status, h.status)}] "
                    f"**{h.subject or '(sans objet)'}** · {who} · {when}"
                    + (f" · {h.n_offers} offre(s)" if h.n_offers else "")
                )


def _mail_row(r) -> None:
    label = STATUS_LABELS.get(r.status, r.status)
    color = STATUS_COLORS.get(r.status, "gray")
    when = f"{r.received_at:%d/%m/%Y %H:%M}" if r.received_at else "—"
    who = r.user_label or r.sender or "expéditeur inconnu"
    with st.container(border=True):
        st.markdown(f":{color}-badge[{label}] **{r.subject or '(sans objet)'}**  \n:gray[{who} · {when}]")
        if r.postings:
            st.markdown(
                "\n".join(
                    f"- [{p.title}]({p.url}) — {p.company or '?'} · {p.location or '?'}"
                    + (f" · {p.criteria['Contrat']}" if p.criteria.get("Contrat") else "")
                    for p in r.postings
                )
            )
