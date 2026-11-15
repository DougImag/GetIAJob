# getiajob

> Trouver rapidement un job qui correspond à **mon CV** et à **mes attentes**, grâce à un pipeline de trois agents IA basés sur des modèles open source.

`getiajob` lit ton CV, va chercher les offres récentes sur **LinkedIn, France Travail et Adzuna**, lit tes **alertes e-mail APEC**, puis te sort un classement argumenté des meilleures offres (score, points forts, points de vigilance, compétences manquantes). Il s'utilise via une **interface web** (Streamlit, multi-utilisateur, déployable en local ou en ligne) ou en **ligne de commande**.

---

## Sommaire

- [Architecture](#architecture)
- [Interface web](#interface-web)
- [Avis sur les offres](#avis-sur-les-offres)
- [Stack technique](#stack-technique)
- [Démarrage rapide](#démarrage-rapide)
- [Utilisation en ligne de commande](#utilisation-en-ligne-de-commande)
- [Mise en ligne](#mise-en-ligne)
- [Configuration](#configuration)
- [Base de données](#base-de-données)
- [Développement (devcontainer VS Code)](#développement-devcontainer-vs-code)
- [Structure du projet](#structure-du-projet)
- [Relève planifiée des alertes e-mail (cron)](#relève-planifiée-des-alertes-e-mail-cron)
- [Supervision automatique et sauvegardes (cron)](#supervision-automatique-et-sauvegardes-cron)
- [Limites et avertissements](#limites-et-avertissements)
- [Feuille de route](#feuille-de-route)
- [Journal des évolutions](#journal-des-évolutions)

---

## Architecture

Le pipeline est orchestré par **LangGraph** : chaque agent est un nœud du graphe, ils partagent un état commun (`PipelineState`) et toutes les données utiles sont persistées dans PostgreSQL. L'interface web et la CLI déclenchent le même graphe.

```mermaid
flowchart LR
    UI[Interface web<br/>Streamlit] -->|session de recherche| RUN
    CLI[CLI getiajob] --> RUN
    subgraph RUN [LangGraph]
        A1["Agent 1<br/>Analyseur de CV"] -->|profil structuré| A2["Agent 2<br/>Collecteur d'offres"]
        A1 -.->|sans collecte| A3
        A2 -->|offres récentes| A3["Agent 3<br/>Trieur"]
    end
    A3 --> OUT[/Top offres/]
    RUN <--> DB[(PostgreSQL<br/>+ pgvector)]
    RUN <--> LLM[[Ollama<br/>LLM + embeddings]]
```

| Agent | Module | Rôle |
|---|---|---|
| **1. Analyseur de CV** | `cv_text.py` + `agents/cv_analyzer.py` | **Lecture fidèle à la mise en page** : colonnes détectées et lues l'une après l'autre, titres de sections reconnus et balisés. Puis **une extraction LLM par section**, en parallèle (identité/coordonnées, expériences, formation, projets, compétences/langues/intérêts), suivie d'une **synthèse** (séniorité, années d'expérience, postes à cibler, secteurs). Des **garde-fous** suppriment les dates et éléments absents du CV et complètent les coordonnées par expressions régulières. Le fichier, le profil et son embedding sont stockés ; un CV inchangé n'est pas ré-analysé. |
| **2. Collecteur d'offres** | `agents/job_scraper.py` + `scraping/` | Le LLM génère des requêtes de recherche à partir du profil et des attentes (le **poste indiqué dans la fiche passe toujours en premier**), sauf si des requêtes sont imposées. Chaque requête est envoyée, pour chaque lieu, aux **sources choisies** : **LinkedIn** (pages publiques, filtres télétravail / niveau d'expérience, détail de chaque nouvelle offre), **France Travail** (API officielle *Offres d'emploi v2* : description complète, contrat, salaire, offres de sites partenaires) et **Adzuna** (API officielle, agrégateur de nombreux sites et cabinets ; extrait de description seulement). **APEC** : pas de recherche en direct (site protégé contre les robots) ; les alertes e-mail APEC que chaque utilisateur transfère vers la boîte commune de GetIAJob sont relevées en IMAP et leurs offres sont **rattachées à l'expéditeur** (adresse de son compte, authentifiée SPF/DKIM) : elles ne sont visibles que de lui. Une source sans clé ou en panne est ignorée sans bloquer les autres. Les offres sont dédupliquées en base, **y compris entre sources** (même intitulé + même entreprise vus dans les 45 derniers jours : une seule offre, complétée par la description la plus longue). |
| **3. Trieur** | `agents/ranker.py` | En trois étages : **(1)** filtres durs (mots-clés exclus/obligatoires, entreprises exclues) ; **(2)** pré-sélection sémantique par similarité cosinus CV ↔ offre via **pgvector** ; **(3)** évaluation détaillée par le LLM des `K` meilleures (note /100, pour/contre, compétences en phase / manquantes). Score final = `llm_weight × note LLM + (1 − llm_weight) × similarité`. Les évaluations sont mises en cache et recalculées seulement si tes attentes, le modèle ou le texte de l'offre changent. |

Depuis l'interface web, une recherche ne trie **que les offres collectées par cette recherche** ; ses paramètres et son classement sont figés dans l'historique.

## Interface web

Application Streamlit avec une **page d'accueil** publique et un **espace connecté** composé d'un header, d'un menu à gauche et d'une zone centrale. Les icônes sont des Material Symbols (pas d'emojis).

- **Header** fixe, sur toute la largeur de la fenêtre : à gauche le **logo et le nom GetIAJob, qui ramènent à l'accueil** (icône [Lucide *briefcase-business*](https://lucide.dev/icons/briefcase-business), licence ISC), puis le **nom de la page courante** ; à droite le **bouton sombre / clair** (lune / soleil) et le **menu utilisateur** (nom d'utilisateur + menu déroulant : *Mon compte*, *Se déconnecter*), ou un bouton **Se connecter / S'inscrire** si on n'est pas connecté.
- **Thème sombre / clair** : l'icône du header bascule l'affichage (thèmes `[theme.light]` et `[theme.dark]` de `.streamlit/config.toml`, styles maison adaptés au mode sombre). Le choix est mémorisé dans le navigateur pour toutes les pages ; sans choix, l'application suit le réglage du système.
- **Menu de gauche** (une fois connecté, avec en pied de page les modèles Ollama utilisés) : *Mes ressources* (Tableau de bord, Mon profil), *Recherche* (Nouvelle recherche, Mes recherches, Mes candidatures) et, pour les administrateurs, *Admin* (Supervision, Qualité & IA, Utilisateurs, Sources d'annonces). Il est masqué sur la page d'accueil et absent tant qu'on n'est pas connecté. Il est **déplié automatiquement à chaque connexion** (même s'il avait été replié avant une déconnexion), sauf sur mobile.

| Page | Contenu |
|---|---|
| **Accueil** | Page de présentation soignée : hero avec aperçu illustratif d'un classement, chiffres clés, fonctionnalités (icônes Lucide), étapes en frise, **encart « Tes alertes e-mail aussi »** (comment rediriger ses alertes APEC vers la boîte de GetIAJob depuis l'adresse de son compte, affiché si la boîte IMAP est configurée), encart confidentialité, puis, pour un visiteur, le **bloc « Accès sur invitation »** (parcours en 4 étapes et formulaire de demande : nom, email, projet facultatif) à la place de l'appel à l'action ; sans menu latéral (connexion / inscription par le bouton du header ; une fois connecté, raccourcis *Lancer une recherche* et *Tableau de bord*) |
| **Connexion** | Connexion ou création de compte (email + mot de passe), option « rester connecté », SSO optionnel. En adhésion sur invitation, la création de compte demande le **code d'invitation** (pré-rempli, ainsi que l'adresse, par le lien reçu par e-mail) et propose le formulaire de demande d'accès |
| **Tableau de bord** | Bandeau d'accueil (salutation selon l'heure, objectif, lieux / mode de travail / fraîcheur en pastilles, raccourcis *Nouvelle recherche*, *Mes recherches*, *Ma fiche*), chiffres clés en cartes (complétude de la fiche, CV et état de l'analyse, recherches dont en cours, meilleur score, avis donnés), **prochaines étapes** avec barre de progression (charger le CV, le faire analyser, compléter la fiche, lancer une recherche), **meilleures offres** de la dernière recherche (score en anneau, entreprise, lieu, lien), **dernières recherches** (statut, date, offres collectées, meilleur score, bouton *Voir*) |
| **Mon profil** | Une seule page, trois onglets dans la zone centrale :<br>• **CV** — import (PDF, DOCX, MD, TXT) et analyse par l'IA **en arrière-plan**. Carte d'identité (initiales, titre, niveau, coordonnées cliquables, résumé, postes ciblés) puis onglets : Expériences (frise chronologique : chiffres clés, une carte par poste avec entreprise, lieu, dates, durée, contrat (**CDI par défaut** si le CV ne le précise pas, sauf indice de stage, alternance, freelance, intérim ou CDD dans l'intitulé ou l'employeur), badge « En poste », réalisations cochées et technologies), Formation (frise des diplômes + certifications en cartes), Projets (grille de cartes avec lien et technologies), Compétences (une carte par catégorie, catégories en double fusionnées, savoir-être à part), Langues (jauge selon le niveau) & centres d'intérêt, **Texte extrait** (sections détectées en pastilles). **Correction manuelle** : interrupteur « Modifier l'identité » et « Modifier ce bloc » sur chaque onglet (expériences — contrat, dates, réalisations… —, formation, projets, compétences en tableau, langues & intérêts) ; badge « Corrigé manuellement », scores recalculés à la prochaine recherche. Téléchargement, ré-analyse (avec confirmation si le CV a été corrigé), suppression<br>• **Fiche de recherche** — récapitulatif en tête (jauge de complétude, critères actuels en pastilles, éléments manquants en orange) puis quatre blocs : *Le poste* (poste, lieu(x), **fraîcheur des annonces** de 24 h à 1 mois, **sources d'annonces**), *Les conditions* (mode de travail, contrat et niveau en pastilles cliquables, salaire minimum), *Ce qui compte pour moi* (attentes en texte libre), *Exclusions* (mots-clés, entreprises)<br>• **Mes avis** — offres aimées / écartées, motifs et commentaires, aperçu de ce que l'IA retiendra, suppression d'un avis |
| **Nouvelle recherche** | Recherche personnalisée : fraîcheur des annonces (par défaut celle de la fiche), nombre de résultats, requêtes, lieux, **sources d'annonces** (LinkedIn, France Travail, Adzuna), télétravail, niveau, mots-clés obligatoires/exclus, entreprises exclues ; options avancées (nb de requêtes, offres par requête, offres évaluées par l'IA, poids de la note IA, sans collecte, ignorer le cache) |
| **Mes recherches** | Historique et suivi en direct. Détail d'une recherche : en-tête (statut, date, CV utilisé, critères en pastilles), chiffres clés (offres collectées / classées, meilleur score, score moyen, durée), filtres par pertinence et score minimum. Chaque offre : score en anneau coloré, entreprise · lieu · ancienneté · salaire, verdict et synthèse, compétences clés ; *Analyse détaillée* avec points forts / de vigilance, compétences en adéquation / à renforcer et décomposition du score (note IA, proximité CV ↔ offre, ajustement selon les avis) ; bouton *Voir l'offre*. Export Markdown. Bouton **J'ai postulé** (date + commentaire ; une fois enregistré, devient « Postulé le … » et ouvre le suivi). **Avis sur chaque offre** (« J'aime » / « Pas pour moi » + motifs + commentaire), masquage des offres écartées, bouton **Réajuster avec mes avis** |
| **Mes candidatures** | Suivi des offres auxquelles on a postulé. Chiffres clés (candidatures, en cours, échanges / entretiens, **taux de réponse**, **score moyen**), encarts **À venir** (prochaines étapes datées) et **À relancer** (sans nouvelles depuis 10 jours), filtre *En cours / Clôturées / Toutes*, **tri** (candidature récente / ancienne, meilleur score, prochaine étape) et recherche par poste ou entreprise. Chaque candidature : **date de candidature en évidence** (pavé calendrier coloré selon le statut, avec « il y a N j »), **score de l'offre** en anneau et synthèse de l'IA, poste (lien vers l'offre), entreprise, lieu, salaire, source, **frise d'avancement** (Candidature → Entretiens → Offre → Décision, seules les étapes réellement atteintes sont marquées), **statut** modifiable (Postulé, Entretiens en cours, Offre reçue, Acceptée, Refusée, Abandonnée), **prochaine étape** datée (en rouge si dépassée) et **journal de suivi** : entretiens (RH, manager, technique), échanges téléphoniques, tests, relances, retours, notes libres, chacun daté et commenté, modifiable et supprimable ; les changements de statut y sont consignés automatiquement, et le premier entretien fait passer la candidature en « Entretiens en cours ». Ajout d'une **candidature saisie à la main** (offre trouvée ailleurs), modification et suppression |
| **Admin › Utilisateurs** | *Réservé aux administrateurs.* Deux onglets : **Comptes** et **Demandes d'accès**. *Demandes d'accès* : demandes *À traiter* (nom, email, date, message) avec **Accepter** (envoie l'invitation par e-mail) ou **Refuser**, *Invitations* en cours ou expirées (**Renvoyer** avec un nouveau code, **Annuler**), *Historique* (comptes créés, refus, annulations), formulaire **Inviter une adresse** sans demande préalable ; si l'e-mail ne part pas, le lien et le code sont affichés pour être transmis à la main. *Comptes* : liste des comptes (initiales, nom, email, rôle, mode de connexion, date d'inscription, dernière connexion, nombre de CV / recherches / avis), recherche par nom ou email, filtre Tous / Admins / Utilisateurs, **Consulter** un compte en **lecture seule** (voir l'application exactement comme l'utilisateur : tableau de bord, CV, fiche, avis, recherches, candidatures), **donner ou retirer le rôle admin**, **supprimer un compte** et toutes ses données (avec confirmation). Un administrateur ne peut ni modifier son propre rôle ni supprimer son propre compte |
| **Admin › Sources d'annonces** | *Réservé aux administrateurs.* Une carte par site d'offres (LinkedIn, France Travail, Adzuna) : type d'accès (scraping ou API officielle), statut **Active** / **Clés manquantes**, variables `.env` à renseigner et lien d'inscription, offres en base, vues ces 7 derniers jours, dernière collecte. Formulaire **Tester une recherche** : source, mots-clés, lieu, fraîcheur, nombre d'offres (≤ 30) ; interroge la source en direct **sans rien enregistrer ni lancer l'IA** et affiche durée, nombre d'offres, présence des descriptions et la liste (titre cliquable, entreprise, lieu, date, salaire, critères, identifiant, extrait), ou l'erreur exacte (ex. identifiants refusés) |
| **Admin › Supervision** | *Réservé aux administrateurs.* Bandeau des **alertes ouvertes** puis quatre onglets :<br>• **Activité** (7 / 30 / 90 jours) — recherches, taux d'échec, durée moyenne, utilisateurs actifs à 7 et 30 jours, nouveaux comptes et CV, graphique des recherches par jour et par statut, analyses de CV en échec avec leur erreur<br>• **Recherches** — file de traitement (en cours / en attente, recherches **bloquées** au-delà de `STUCK_SEARCH_MINUTES`) avec bouton **Arrêter**, derniers échecs avec leur message, filtre par utilisateur et bouton **Relancer**<br>• **Santé & alertes** — PostgreSQL (taille de la base, tables les plus volumineuses), Ollama (joignable, modèles installés et chargés, modèles manquants), dernière relève des mails, dernière sauvegarde, test de connexion IMAP à la demande, version et date de démarrage ; liste des alertes (ouvertes et résolues sur 7 jours), boutons **Vérifier maintenant** (sans rien envoyer) et **Vérifier et notifier**<br>• **Journal d'audit** — actions des administrateurs et automatiques (rôles, suppressions de comptes, recherches arrêtées / relancées, purges, imports de mails, sauvegardes, notifications), filtrable par action |
| **Admin › Qualité & IA** | *Réservé aux administrateurs.* Quatre onglets sur la période choisie :<br>• **Offres** — stock (publiques / privées), offres revues sur 7 jours, âge moyen, doublons fusionnés entre sources, offres sans description / embedding / entreprise, nouvelles offres par jour et par source, tableau par source (part sans description, part avec salaire, dernière nouvelle offre), **purge** des offres non revues depuis N jours (les offres ayant un avis ou figurant dans une recherche enregistrée sont conservées)<br>• **Classement** — répartition des notes de l'IA, note moyenne, avis 👍 / 👎 et taux de 👍 (global et par source), motifs de rejet les plus fréquents<br>• **Consommation IA** — appels au LLM et erreurs, appels par jour, taux de cache des évaluations, tableau par étape (appels, éléments, durée moyenne et p95)<br>• **Sources** — requêtes HTTP, erreurs, réponses 429 (blocage probable), temps de réponse, recherches en échec / sans résultat, dernier succès, échecs consécutifs, jauge du quota Adzuna du jour |
| **Mon compte** | Nom affiché, changement de mot de passe, déconnexion (accessible depuis le menu utilisateur du header) |

**Sessions utilisateur**
- **Consultation en lecture seule** (*Admin › Utilisateurs › Consulter*) : l'administrateur voit l'application comme l'utilisateur choisi, avec un bandeau « Lecture seule » et un bouton *Revenir à mon compte*. Le menu Admin est masqué pendant la consultation. **Aucune écriture n'est possible** : un garde-fou au niveau de la connexion PostgreSQL refuse toute requête de modification (INSERT, UPDATE, DELETE…) pendant la consultation, quelle que soit la page ou l'action ; une action tentée affiche « Action impossible : tu consultes ce compte en lecture seule ». Le début et la fin de chaque consultation sont tracés dans le journal d'audit. Le cookie de l'administrateur n'est pas modifié : recharger la page ou se déconnecter met fin à la consultation.
- **Rôle administrateur** (`users.is_admin`) : donne accès au menu *Admin*. Le tout premier compte créé sur une base vide devient administrateur ; les suivants sont des comptes standard. Le rôle est relu en base à chaque action : un droit donné ou retiré s'applique sans reconnexion, et la page Admin est introuvable pour un non-administrateur.
- **Adhésion sur invitation** (`INVITE_ONLY=true`, par défaut) : un visiteur demande un accès (accueil ou page de connexion) ; les administrateurs (ou `ALERT_EMAILS`) sont prévenus par e-mail ; un administrateur accepte et un **code à usage unique**, valable `INVITATION_DAYS` jours, est envoyé avec un lien `PUBLIC_URL/connexion?invitation=…`. Le compte doit être créé avec **l'adresse invitée** ; seul le hash SHA-256 du code est stocké et il est consommé dans la même transaction que la création du compte. En SSO, une invitation valide pour l'adresse suffit. Le tout premier compte (base vide) n'a pas besoin d'invitation. Garde-fou anti-spam : 20 demandes par heure au plus, une seule demande en attente par adresse. Demandes, invitations, refus et annulations sont tracés dans le journal d'audit.
- Mots de passe hachés avec **bcrypt** ; chaque utilisateur ne voit que ses propres données (fiche, CV, recherches).
- **« Rester connecté »** : un jeton aléatoire est placé dans un cookie (`SameSite=Strict`, `Secure` en HTTPS) et seul son hash SHA-256 est stocké en base (`auth_sessions`). Il expire après `SESSION_DAYS` jours et la déconnexion le révoque.
- **SSO optionnel** (Google, Microsoft, Auth0… tout fournisseur OpenID Connect) via `st.login`. Il suffit de copier `.streamlit/secrets.toml.example` en `.streamlit/secrets.toml` et de le remplir.

**Tâches de fond** : les recherches et les analyses de CV tournent **côté serveur** (`services/runner.py`), dans deux files distinctes d'un worker chacune (le LLM local ne parallélise pas). On peut changer de page, d'onglet ou de fenêtre, voire fermer le navigateur : l'avancement est enregistré en base et l'interface le rafraîchit toutes les 3 s. Au redémarrage du serveur, les recherches en attente sont relancées (celles en cours sont marquées en échec) et les analyses de CV interrompues sont relancées. Une recherche ne peut pas être lancée avec un CV dont l'analyse est en cours.

## Avis sur les offres

Sous chaque offre proposée, deux boutons permettent de dire à l'IA ce que l'on cherche vraiment :

- **J'aime** : commentaire optionnel (« ce qui me plaît »).
- **Pas pour moi** : motifs à cocher (poste hors cible, niveau trop junior / senior, technologies, secteur, entreprise, localisation, télétravail, salaire, contrat, déjà vue), commentaire libre (« pourquoi ») et option *ne plus me proposer cette entreprise* (ajoutée aux exclusions de la fiche).

Ces avis sont réutilisés par les recherches suivantes, en limitant le calcul :

| Mécanisme | Effet | Coût |
|---|---|---|
| **Exclusion** | Une offre écartée n'est plus jamais proposée | Aucun (et une évaluation LLM en moins) |
| **Ajustement sémantique** | Les offres proches d'une offre aimée gagnent jusqu'à 10 points, celles proches d'une offre écartée *sur le fond* (poste, niveau, technologies, secteur, ou sans motif) en perdent jusqu'à 10. Appliqué dès la pré-sélection, il change aussi les offres envoyées au LLM | Aucun appel LLM : embeddings déjà en base (produit scalaire) |
| **Résumé pour l'IA** | Quelques lignes déterministes (motifs fréquents, offres aimées / écartées avec commentaires, ≤ 1 200 caractères) ajoutées aux attentes, lues par l'agent qui génère les requêtes et par celui qui note les offres | Quelques centaines de tokens par évaluation ; les offres déjà notées sont ré-évaluées quand les avis changent. Désactivable dans *Nouvelle recherche* |

Deux offres sont considérées « du même genre » au-delà d'une similarité cosinus de 0,78 (effet maximal à 0,95). Seuils choisis d'après la distribution réelle des offres : médiane 0,72, 90e centile 0,80. Sur 170 offres, un avis aimé + un avis écarté ajustent typiquement une quinzaine d'offres.

**Réajuster avec mes avis** crée une nouvelle recherche avec les mêmes critères et les avis actuels, **sans interroger les sites d'annonces** : seul le tri est refait.

## Stack technique

- **Python 3.12** ; CLI avec Typer + Rich ; **interface web Streamlit**
- **LangChain** (`langchain-ollama`) pour les modèles, **LangGraph** pour l'orchestration
- **Ollama** pour servir les modèles open source :
  - LLM par défaut : `qwen2.5:7b` (bon en français et en sortie JSON structurée)
  - Embeddings : `nomic-embed-text` (768 dimensions)
- **PostgreSQL 16 + pgvector** (SQLAlchemy 2 / psycopg 3)
- **PyMuPDF** (lecture des PDF avec positions, licence AGPL) et **python-docx** pour les CV
- **httpx + BeautifulSoup** pour le scraping, **tenacity** pour les reprises sur erreur
- **bcrypt** (mots de passe), **Authlib** (SSO OIDC optionnel)
- **Docker / Docker Compose**, **Caddy** (HTTPS en production), **devcontainer** VS Code

## Démarrage rapide

### Prérequis

- Docker Desktop (avec Docker Compose v2.24+)
- [Ollama](https://ollama.com) installé sur la machine hôte (**recommandé sur Mac** : Docker n'a pas accès au GPU Apple Silicon, les modèles tournent bien plus vite en natif)

### Étapes

```bash
# 1. Configuration
cp .env.example .env

# 2. Modèles (Ollama natif)
ollama pull qwen2.5:7b
ollama pull nomic-embed-text

# 3. Base de données + interface web
docker compose up -d --build db web
docker compose run --rm app doctor      # vérifie DB + Ollama + modèles
```

Ouvre **http://localhost:8501**, crée ton compte, complète ta fiche, importe ton CV et lance une recherche.

> Une base créée en v0.1 est mise à niveau automatiquement au démarrage (ajout des tables utilisateurs/sessions, CV liés aux utilisateurs) sans perte de données. Les CV importés via la CLI restent utilisables par la CLI.

<details>
<summary>Variante : Ollama dans Docker (Linux / GPU NVIDIA ou machine sans Ollama natif)</summary>

```bash
# dans .env : OLLAMA_BASE_URL=http://ollama:11434
docker compose --profile ollama up -d ollama db web
docker compose run --rm app pull-models
```
Pour un GPU NVIDIA, ajouter au service `ollama` une réservation `deploy.resources.reservations.devices` (cf. doc Docker).
</details>

<details>
<summary>Variante : sans Docker pour l'application</summary>

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
export DATABASE_URL=postgresql+psycopg://getiajob:getiajob@localhost:5432/getiajob
export OLLAMA_BASE_URL=http://localhost:11434
getiajob web          # http://localhost:8501
```
</details>

## Utilisation en ligne de commande

```text
getiajob web [--port 8501]       Lance l'interface web
getiajob init-db                 Crée/met à niveau les tables (idempotent)
getiajob doctor                  Vérifie PostgreSQL, Ollama et la présence des modèles
getiajob pull-models             Télécharge dans Ollama les modèles configurés
getiajob analyze-cv CV [--force] Agent 1 seul : affiche le profil extrait (JSON)
getiajob run CV                  Pipeline complet (agents 1 → 2 → 3), préférences de config/preferences.yaml
    --skip-scrape                  ne réinterroge pas les sources, trie les offres déjà en base
    --force-cv                     ré-analyse le CV même s'il est inchangé
    --force-rank                   ré-évalue toutes les offres avec le LLM
getiajob top [--limit 10]        Ré-affiche le dernier classement
getiajob mail-sync [--dry-run] [--quiet]   Relève les alertes e-mail (APEC)
getiajob monitor [--dry-run] [--no-notify] [--quiet]   Supervision : alertes + e-mail aux admins
getiajob record-backup FICHIER --size N    Enregistre une sauvegarde (appelé par scripts/backup-db.sh)
getiajob -v ...                  Logs détaillés (dont les raisons d'exclusion)
```

Avec Docker : préfixer par `docker compose run --rm app`. Dans le devcontainer : appeler `getiajob` directement.

Pour automatiser une exécution hebdomadaire, une simple tâche cron :

```cron
0 8 * * 1  cd /chemin/vers/getiajob && docker compose run --rm app run data/cv/mon_cv.pdf
```

## Mise en ligne

La même image Docker sert en local et en ligne. Exemple sur un VPS (avec Docker installé et un nom de domaine qui pointe vers le serveur) :

```bash
git clone … && cd getiajob
cp .env.example .env
# Dans .env : POSTGRES_PASSWORD fort, DOMAIN=getiajob.mondomaine.fr,
#             ALLOW_SIGNUP=false une fois ton compte créé, OLLAMA_BASE_URL selon ton serveur Ollama
docker compose --profile prod up -d --build db web proxy
```

Le service **`proxy` (Caddy)** obtient automatiquement un certificat **HTTPS Let's Encrypt** et relaie vers Streamlit (WebSockets compris). En production :

- ne publie pas le port PostgreSQL : retire `ports` du service `db` ou limite-le à `127.0.0.1:5432:5432` ;
- ne publie que 80/443 : limite le port `web` à `127.0.0.1:8501:8501` ;
- prévois un serveur **Ollama avec GPU** (sur la même machine via le profil `ollama`, ou un serveur dédié) : en CPU, l'évaluation des offres est très lente ;
- pour le SSO, monte `.streamlit/secrets.toml` dans le service `web` (ligne commentée dans `docker-compose.yml`) avec `redirect_uri = "https://ton-domaine/oauth2callback"`.

### Déploiement continu (GitHub Actions → VPS)

Le workflow `.github/workflows/ci-cd.yml` :

1. **à chaque push et pull request** : `ruff check`, `ruff format --check`, `pytest` ;
2. **sur `main`** : construit l'image (`target: runtime`) et la publie sur **GHCR** (`ghcr.io/<owner>/getiajob`, tags `latest` et SHA du commit) ;
3. **déploie** par SSH : sur le VPS, `git reset --hard <sha>` (fichiers compose, Caddyfile, scripts), puis `deploy/deploy.sh <sha>` (`docker compose pull` + `up -d`, attente de l'état *healthy*, sinon échec du job avec les derniers journaux). La base (`pgdata`), `.env` et `data/` ne sont jamais touchés ; le schéma est mis à niveau au démarrage. Une recherche en cours au moment du déploiement est marquée en échec.

`docker-compose.prod.yml` remplace la construction locale par l'image GHCR, ferme le port PostgreSQL et limite Streamlit à `127.0.0.1`. Il est activé sur le serveur par une ligne du `.env` : `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` — les scripts cron fonctionnent ainsi sans changement.

**Proxy HTTPS**, au choix :
- **Apache déjà présent sur le serveur** (sous-domaine d'un domaine existant) : modèle de VirtualHost `deploy/apache-getiajob.conf` (proxy HTTP + WebSocket vers `127.0.0.1:8501`, certificat par `certbot --apache`). Ne pas activer le profil `prod` : Caddy n'est pas lancé et les ports 80/443 restent à Apache ;
- **Caddy fourni** (serveur dédié) : ajouter `COMPOSE_PROFILES=prod` et `DOMAIN=…` au `.env` ; `deploy.sh` démarre alors aussi le service `proxy`.

**Mise en place (une fois)**

```bash
# Sur le VPS (Debian/Ubuntu), en root
curl -fsSL https://get.docker.com | sh
adduser --disabled-password --gecos "" deploy && usermod -aG docker deploy
ufw allow OpenSSH && ufw allow 80,443/tcp && ufw enable
mkdir -p /opt/getiajob && chown deploy: /opt/getiajob
su - deploy -c "git clone https://github.com/<owner>/GetIAJob.git /opt/getiajob"
# puis /opt/getiajob/.env (cf. .env.example) avec en plus :
#   COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
#   PUBLIC_URL=https://getiajob.mondomaine.fr
#   POSTGRES_PASSWORD=<fort>        OLLAMA_BASE_URL=<serveur Ollama>
#   (avec Caddy plutôt qu'Apache : COMPOSE_PROFILES=prod et DOMAIN=getiajob.mondomaine.fr)

# Apache : sous-domaine en proxy vers Streamlit
sudo a2enmod proxy proxy_http proxy_wstunnel headers rewrite ssl
sudo cp /opt/getiajob/deploy/apache-getiajob.conf /etc/apache2/sites-available/getiajob.conf
# (remplacer getiajob.mondomaine.fr par le sous-domaine dans ce fichier)
sudo a2ensite getiajob && sudo apache2ctl configtest && sudo systemctl reload apache2
sudo certbot --apache -d getiajob.mondomaine.fr

# Sur ton poste : clé dédiée au déploiement
ssh-keygen -t ed25519 -f ~/.ssh/getiajob_deploy -C "github-actions-deploy" -N ""
ssh-copy-id -i ~/.ssh/getiajob_deploy.pub deploy@<IP_VPS>
ssh-keyscan -H <IP_VPS>      # → secret VPS_KNOWN_HOSTS
```

Dans GitHub, **Settings › Environments › New environment** `production`, puis ses secrets : `VPS_HOST`, `VPS_USER` (`deploy`), `VPS_SSH_KEY` (contenu de `~/.ssh/getiajob_deploy`), `VPS_KNOWN_HOSTS` ; `VPS_PORT` (port SSH s'il n'est pas 22) et `VPS_PATH`, facultatifs, en variable ou en secret. Un enregistrement DNS du sous-domaine (**A** vers l'IP du VPS, ou **CNAME** vers le domaine principal s'il est hébergé sur ce VPS) doit exister avant `certbot` (ou avant le premier démarrage de Caddy).

**Retour arrière** : sur le VPS, `deploy/deploy.sh <sha d'un commit précédent>` (ou relancer le workflow d'un ancien commit depuis l'onglet *Actions*).

> Docker Compose ≥ 2.24 est requis sur le serveur (balises `!reset` / `!override`), ce qu'installe le script officiel `get.docker.com`.

> **Streamlit Community Cloud** n'est pas adapté : l'application a besoin de PostgreSQL, d'Ollama et de tâches longues en arrière-plan.

## Configuration

### Variables d'environnement (`.env`)

| Variable | Défaut | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | URL d'Ollama |
| `LLM_TIMEOUT` | `900` | Durée maximale (s) d'un appel au LLM ; au-delà, l'étape est abandonnée |
| `LLM_MODEL` | `qwen2.5:7b` | Modèle de chat (ex. `llama3.1:8b`, `mistral-nemo`, `qwen2.5:14b`) |
| `LLM_NUM_CTX` | `8192` | Fenêtre de contexte envoyée à Ollama (le défaut d'Ollama tronque les CV/offres longs) |
| `LLM_CONCURRENCY` | `2` | Appels LLM simultanés lors de l'analyse d'un CV (à aligner sur `OLLAMA_NUM_PARALLEL`) |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Modèle d'embedding |
| `EMBEDDING_DIM` | `768` | Dimension des vecteurs, **doit correspondre au modèle d'embedding** |
| `LINKEDIN_REQUEST_DELAY` | `2.5` | Délai moyen (s) entre deux requêtes LinkedIn |
| `FRANCETRAVAIL_CLIENT_ID` / `FRANCETRAVAIL_CLIENT_SECRET` | *(vide)* | Identifiants d'une application [francetravail.io](https://francetravail.io) **ayant accès à l'API « Offres d'emploi v2 »**. Attention : cette API n'est plus proposée en libre-service (accès restreint, sur demande). Vide = source France Travail désactivée |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | *(vide)* | Clés gratuites de [developer.adzuna.com](https://developer.adzuna.com) (quota journalier limité). Vide = source Adzuna désactivée |
| `ADZUNA_REQUEST_DELAY` | `1.0` | Délai (s) entre deux appels Adzuna |
| `IMAP_HOST` / `IMAP_PORT` / `IMAP_USER` / `IMAP_PASSWORD` / `IMAP_FOLDER` | *(vide)* / `993` / … / `INBOX` | Boîte commune recevant les alertes APEC transférées (ex. Gmail : `imap.gmail.com` + mot de passe d'application). Vide = source APEC désactivée |
| `MAIL_LOOKBACK_DAYS` | `31` | Ancienneté maximale des mails analysés |
| `MAIL_DELETE_PROCESSED` | `true` | Supprime de la boîte les alertes importées (déplacées dans la corbeille, ex. vidée après 30 jours chez Gmail). `false` = boîte en lecture seule |
| `ALERTS_ENABLED` | `true` | Envoi des notifications e-mail de supervision (`false` = alertes visibles seulement dans l'admin) |
| `ALERT_EMAILS` | *(vide)* | Destinataires des alertes, séparés par des virgules. Vide = tous les comptes administrateurs |
| `ALERT_REMIND_HOURS` | `24` | Rappel d'une alerte toujours ouverte |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | *(vide)* / `465` / … | Envoi des alertes. Vides = déduits de la boîte IMAP (`imap.gmail.com` → `smtp.gmail.com`, mêmes identifiants). Port 465 = SSL, autre = STARTTLS |
| `STUCK_SEARCH_MINUTES` | `30` | Au-delà, une recherche en cours est signalée « bloquée » |
| `MAIL_STALE_MINUTES` | `60` | Alerte si la relève des mails n'a pas réussi depuis ce délai (seulement si elle a déjà tourné) |
| `SOURCE_FAILURE_THRESHOLD` | `3` | Échecs consécutifs d'une source avant alerte |
| `SEARCH_FAILURE_RATE` | `0.5` | Taux d'échec des recherches sur 24 h déclenchant une alerte (à partir de 4 recherches) |
| `BACKUP_STALE_HOURS` | `48` | Alerte si la dernière sauvegarde est plus ancienne (seulement si une sauvegarde a déjà été enregistrée) |
| `ADZUNA_DAILY_QUOTA` | `250` | Quota journalier indicatif d'Adzuna (jauge et alerte à 90 %) |
| `METRICS_RETENTION_DAYS` | `90` | Durée de conservation des mesures techniques |
| `OFFER_RETENTION_DAYS` | `90` | Valeur proposée par défaut pour la purge des offres |
| `DISPLAY_TIMEZONE` | `Europe/Paris` | Fuseau d'affichage des dates dans l'administration |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `DB_PORT` | `getiajob` / … / `5432` | Base de données |
| `DATABASE_URL` | construit par docker-compose | À définir uniquement pour exécuter hors Docker |
| `WEB_PORT` | `8501` | Port de l'interface web |
| `ALLOW_SIGNUP` | `true` | `false` pour fermer complètement la création de comptes (ni demande, ni invitation) |
| `INVITE_ONLY` | `true` | Adhésion sur invitation (formulaire de demande, code envoyé par un administrateur) ; `false` = inscription libre |
| `INVITATION_DAYS` | `14` | Durée de validité d'une invitation |
| `PUBLIC_URL` | *(vide)* | Adresse publique de l'application (ex. `https://getiajob.mondomaine.fr`), pour le lien des e-mails d'invitation ; vide = code seul |
| `SESSION_DAYS` | `30` | Durée de la connexion « rester connecté » |
| `DOMAIN` | `localhost` | Nom de domaine pour Caddy (profil `prod`) |
| `LOG_LEVEL` | `INFO` | Niveau de logs |

> Changer de modèle d'embedding avec une autre dimension impose de recréer la base (`docker compose down -v`), les colonnes `vector` ayant une taille fixe.

### Préférences

- **Interface web** : valeurs par défaut issues de `config/preferences.yaml`, puis de **ta fiche**, puis du **formulaire de recherche** (chaque niveau surcharge le précédent).
- **CLI** : `config/preferences.yaml` uniquement. Le fichier est relu à chaque exécution et monté en lecture seule dans les conteneurs.
  - **`search`** : `keywords` (vide = générés par l'agent), `locations`, `remote` (`on-site`/`remote`/`hybrid`), `experience_levels`, `days`, `max_queries`, `max_offers_per_query`
  - **`expectations`** : `desired_job`, `start_date`, `description` (texte libre lu par le LLM), `min_salary`, `contract_types`, `must_have_keywords` / `exclude_keywords` / `exclude_companies` (filtres durs, sur mot entier, insensibles à la casse)
  - **`ranking`** : `prefilter_top_k` (nb d'offres évaluées par le LLM), `top_n`, `llm_weight`

## Base de données

PostgreSQL + extension **pgvector**. Les tables sont créées ou mises à niveau par `getiajob init-db`, exécuté automatiquement au démarrage de l'interface web (idempotent).

| Table | Contenu |
|---|---|
| `users` | Compte : email, nom, hash bcrypt (ou fournisseur SSO), rôle administrateur (`is_admin`), dates de création / dernière connexion |
| `auth_sessions` | Sessions « rester connecté » : hash du jeton, expiration |
| `user_profiles` | Fiche de recherche : poste recherché, lieux, fraîcheur des annonces (`max_offer_age_days`), télétravail, contrats, niveaux, salaire, attentes, exclusions |
| `cv_profiles` | CV : utilisateur (NULL = CLI), fichier original, hash, texte extrait (balisé par sections), profil extrait (JSONB versionné : contact, expériences, formation, projets, compétences, langues…), embedding, statut de l'analyse en arrière-plan (`analysis_status`, `analysis_error`) |
| `job_offers` | Offre d'emploi : `source` (linkedin, francetravail, adzuna), identifiant chez la source (colonne `linkedin_id`, préfixé `ft:` / `adz:` hors LinkedIn), `fingerprint` (titre + entreprise, pour les doublons entre sources), titre, entreprise, lieu, URL, date, description, salaire, critères JSONB), requête d'origine, embedding, `first_seen_at` / `last_seen_at` |
| `job_matches` | Couple CV × offre : similarité, note LLM, score final, évaluation détaillée (JSONB), clé de cache |
| `job_feedback` | Avis d'un utilisateur sur une offre : note (+1 / −1), motifs (JSONB), commentaire, recherche d'origine |
| `job_applications` | Candidature : utilisateur, offre (NULL si saisie à la main ou offre purgée), recherche d'origine, copie du poste / entreprise / lieu / lien / source / salaire, score et synthèse de l'IA au moment de la candidature, statut, date de candidature, prochaine étape et sa date |
| `application_notes` | Journal d'une candidature : type (note, entretien RH / manager / technique, échange téléphonique, test, relance, retour, changement de statut), date, commentaire |
| `mail_imports` | Mails d'alerte déjà traités : empreinte du Message-ID, utilisateur rattaché, expéditeur, sujet, statut, nb d'offres |
| `metric_events` | Mesures techniques : appels au LLM et aux embeddings (durée, erreur), évaluations servies par le cache, requêtes HTTP par source (code, durée), collectes par requête, doublons fusionnés, relèves des mails, sauvegardes. Purgées après `METRICS_RETENTION_DAYS` jours |
| `audit_log` | Journal d'audit : date, auteur (email ou « système »), action, cible, détail |
| `monitor_alerts` | Alertes de supervision : clé stable, gravité, message, ouverture, dernière constatation, dernière notification, résolution |
| `search_sessions` | Recherche web : paramètres (JSONB, dont les avis figés au lancement), statut, étape en cours, erreur, nb d'offres, résultats figés (JSONB), horodatages |

Pour s'y connecter depuis un client SQL (DBeaver, SQLTools…) : `localhost:5432`, base/utilisateur/mot de passe `getiajob` (valeurs du `.env`). Exemple :

```sql
SELECT s.name, s.status, s.created_at, jsonb_array_length(s.results) AS nb_resultats
FROM search_sessions s JOIN users u ON u.id = s.user_id
ORDER BY s.created_at DESC;
```

## Développement (devcontainer VS Code)

1. Installer l'extension **Dev Containers** dans VS Code.
2. `cp .env.example .env`
3. *Command Palette* → **Dev Containers: Reopen in Container**.

Au démarrage, le devcontainer :
- lance les services `db` et `dev` (image Python avec git, curl, `psql`) ;
- installe le projet en mode éditable avec les dépendances de dev et initialise la base ;
- configure Ruff, Pylance, pytest et SQLTools, et redirige le port **8501**.

Ollama reste accessible sur l'hôte via `host.docker.internal`.

```bash
getiajob web       # interface web → http://localhost:8501
pytest             # tests unitaires (sans DB ni LLM)
ruff check . && ruff format .
psql "$DATABASE_URL" -c '\dt'   # (retirer "+psycopg" de l'URL pour psql)
```

## Structure du projet

```text
getiajob/
├── .devcontainer/                # devcontainer VS Code (compose + config)
├── .streamlit/
│   ├── config.toml               # thème, taille max d'upload, options serveur
│   └── secrets.toml.example      # SSO OpenID Connect (optionnel)
├── config/preferences.yaml       # valeurs par défaut : recherche, attentes, tri
├── deploy/Caddyfile              # reverse proxy HTTPS (profil prod)
├── deploy/deploy.sh              # déploiement sur le VPS (appelé par GitHub Actions)
├── deploy/apache-getiajob.conf   # VirtualHost Apache (reverse proxy HTTPS + WebSocket)
├── .github/workflows/ci-cd.yml   # lint, tests, image GHCR, déploiement
├── data/                         # CV et rapports de la CLI (ignorés par git)
├── src/getiajob/
│   ├── cv_text.py                # lecture des CV : colonnes, ordre de lecture, titres de sections
│   ├── agents/                   # agent 1 (cv_analyzer), 2 (job_scraper), 3 (ranker)
│   ├── scraping/                 # sources d'offres
│   │   ├── base.py               #   offre normalisée, libellés, empreinte anti-doublons
│   │   ├── linkedin.py           #   client + parseurs HTML LinkedIn
│   │   ├── francetravail.py      #   API France Travail (OAuth2, lieux, pagination)
│   │   ├── adzuna.py             #   API Adzuna
│   │   └── apec_mail.py          #   alertes e-mail APEC : IMAP, expéditeur, extraction des offres
│   ├── db/                       # modèles SQLAlchemy, session, mises à niveau du schéma
│   ├── services/                 # logique applicative sans UI
│   │   ├── users.py              #   comptes, mots de passe, sessions persistantes, fiche
│   │   ├── access.py             #   adhésion sur invitation : demandes d'accès, invitations
│   │   ├── cvs.py                #   CV d'un utilisateur, statut de l'analyse
│   │   ├── sources.py            #   statistiques par source, recherche de test (admin)
│   │   ├── mail_alerts.py        #   relève des alertes e-mail, rattachement aux utilisateurs
│   │   ├── searches.py           #   préférences, exécution d'une recherche, réajustement, historique
│   │   ├── applications.py       #   candidatures : « J'ai postulé », statut, prochaine étape, journal des entretiens
│   │   ├── feedback.py           #   avis sur les offres : exclusion, ajustement sémantique, résumé pour l'IA
│   │   ├── runner.py             #   files de tâches de fond (recherches, analyses de CV)
│   │   ├── monitoring.py         #   mesures techniques (LLM, sources, HTTP) et journal d'audit
│   │   ├── supervision.py        #   indicateurs des pages Admin, actions (arrêt, relance, purge)
│   │   └── alerting.py           #   détection des problèmes, cycle de vie des alertes, e-mail
│   ├── web/                      # interface Streamlit
│   │   ├── app.py                #   point d'entrée : navigation, header, menu
│   │   ├── layout.py             #   header, styles, composants
│   │   ├── state.py              #   session utilisateur, cookie, démarrage
│   │   └── views/                #   accueil, connexion, tableau de bord, profil (CV + fiche), recherche, résultats, compte, admin
│   ├── cli.py                    # commandes `getiajob`
│   ├── config.py                 # settings (.env) + préférences
│   ├── graph.py                  # graphe LangGraph
│   ├── llm.py                    # ChatOllama / OllamaEmbeddings
│   ├── report.py                 # tableau console + rapport Markdown
│   └── schemas.py                # sorties structurées des LLM (Pydantic)
├── scripts/                      # tâches cron : cron-run.sh, cron-mail-sync.sh, cron-monitor.sh, backup-db.sh
├── tests/
├── Dockerfile                    # cibles `dev` et `runtime`
├── docker-compose.yml            # db, web, app (CLI), ollama (profil), proxy (profil prod)
├── docker-compose.prod.yml       # surcharge de production (image GHCR, ports fermés)
└── pyproject.toml
```

## Relève planifiée des alertes e-mail (cron)

Les alertes APEC sont relevées à chaque recherche ; pour les importer (et vider la boîte) en continu, même sans recherche, planifie la commande `getiajob mail-sync` :

```bash
getiajob mail-sync            # relève, importe, supprime les alertes traitées
getiajob mail-sync --dry-run  # aperçu, sans rien enregistrer ni supprimer
getiajob mail-sync --quiet    # n'affiche rien s'il n'y a aucun nouveau mail (pour cron)
```

Code de sortie : `0` = OK (ou relève déjà en cours ailleurs), `1` = erreur (boîte injoignable, identifiants refusés…). Un verrou PostgreSQL empêche deux relèves simultanées (cron, recherche web, bouton admin).

Le script `scripts/cron-mail-sync.sh` lance la commande dans le conteneur `web` (ou dans un conteneur `app` éphémère s'il est arrêté), avec un `PATH` adapté à cron, et écrit dans `data/logs/mail-sync.log` uniquement quand il se passe quelque chose ou en cas d'erreur (journal limité aux 2 000 dernières lignes). Exemple, toutes les 15 minutes (`crontab -e`) :

```cron
*/15 * * * * /usr/local/var/www/htdocs/getiajob/scripts/cron-mail-sync.sh
```

Sur macOS, Docker Desktop doit être démarré ; si cron n'a pas accès au dossier du projet, lui accorder l'« Accès complet au disque » (Réglages Système › Confidentialité et sécurité, ajouter `/usr/sbin/cron`).

## Supervision automatique et sauvegardes (cron)

`getiajob monitor` passe en revue l'application et tient à jour les alertes (page *Admin › Supervision*). Il détecte :

| Alerte | Gravité | Condition |
|---|---|---|
| Recherche bloquée | Attention | En cours depuis plus de `STUCK_SEARCH_MINUTES` |
| Échecs de recherche | Critique | ≥ `SEARCH_FAILURE_RATE` d'échecs sur 24 h (à partir de 4 recherches) |
| Analyses de CV en échec | Attention | Au moins une sur 24 h |
| Source en panne | Critique | `SOURCE_FAILURE_THRESHOLD` collectes en échec d'affilée |
| Source limitée | Attention | ≥ 3 réponses HTTP 429 en 48 h (blocage probable, ex. LinkedIn) |
| Quota Adzuna | Attention | ≥ 90 % de `ADZUNA_DAILY_QUOTA` aujourd'hui |
| Relève des mails en retard | Attention | Pas de relève réussie depuis `MAIL_STALE_MINUTES` |
| Mails non rattachés | Attention | Alertes d'un expéditeur inconnu ou non authentifié (7 jours) |
| Sauvegarde ancienne | Attention | Plus de `BACKUP_STALE_HOURS` |
| Ollama indisponible | Critique | Injoignable ou modèle configuré absent |

Chaque alerte est **notifiée une fois** par e-mail aux administrateurs (ou à `ALERT_EMAILS`), **rappelée** toutes les `ALERT_REMIND_HOURS` heures si elle persiste, puis signalée comme **résolue** quand elle disparaît. L'envoi utilise par défaut la boîte Gmail des alertes (mot de passe d'application). La commande purge aussi les mesures plus anciennes que `METRICS_RETENTION_DAYS`.

```bash
getiajob monitor --dry-run    # affiche les problèmes, sans rien enregistrer ni envoyer
getiajob monitor --no-notify  # met à jour les alertes sans e-mail
getiajob monitor --quiet      # n'affiche rien s'il n'y a rien de nouveau (pour cron)
```

Sauvegarde : `scripts/backup-db.sh` exécute `pg_dump` dans le conteneur `db`, écrit `data/backups/getiajob-AAAAMMJJ-HHMMSS.dump`, garde les 14 dernières (`KEEP=…`) et l'enregistre pour la supervision. Restauration : `docker compose exec -T db pg_restore -U getiajob -d getiajob --clean < data/backups/<fichier>`.

Tous les scripts passent par `scripts/cron-run.sh <journal> <commande>` (conteneur `web` ou `app` éphémère, `PATH` adapté à cron, journal `data/logs/<journal>.log` limité à 2 000 lignes). Crontab complète conseillée :

```cron
*/15 * * * * /usr/local/var/www/htdocs/getiajob/scripts/cron-mail-sync.sh
*/10 * * * * /usr/local/var/www/htdocs/getiajob/scripts/cron-monitor.sh
0 3 * * *    /usr/local/var/www/htdocs/getiajob/scripts/backup-db.sh
```

## Limites et avertissements

- **CGU LinkedIn** : le scraping automatisé est contraire aux conditions d'utilisation de LinkedIn. Le projet n'utilise que les pages publiques (sans compte, donc sans risque pour ton profil), avec des délais aléatoires et un volume faible. À réserver à un **usage personnel** ; une instance en ligne ouverte à de nombreux utilisateurs multiplierait les requêtes. Une IP qui envoie trop de requêtes peut être temporairement bloquée (HTTP 429, géré par des reprises avec attente exponentielle).
- **APEC** : uniquement via les alertes e-mail transférées ; l'offre contient l'intitulé, l'entreprise, le contrat et le lieu, sans la description complète (la note de l'IA est donc indicative). Un mail dont l'expéditeur n'a pas de compte, ou n'est pas authentifié (SPF/DKIM), est ignoré.
- **Indeed, Welcome to the Jungle, HelloWork, Cadremploi** : non pris en charge. Ces sites n'ont pas d'API publique et protègent leurs pages contre les robots (captcha) ; GetIAJob ne contourne pas ces protections. Une partie de leurs offres remonte via Adzuna.
- **France Travail** : l'API « Offres d'emploi v2 » a été retirée du catalogue en libre-service de francetravail.io (accès désormais restreint, sur demande via le formulaire de contact ; fiche data.gouv.fr « Restreint »). Le connecteur reste en place et fonctionne pour une application déjà autorisée ; sans cet accès, la source échoue proprement (« invalid_client ») et les autres sources continuent.
- **Adzuna** : l'API ne renvoie qu'un extrait (≈ 500 caractères) de la description ; la note de l'IA est donc moins précise pour ces offres. Quota gratuit limité par jour.
- **HTML fragile** : si LinkedIn modifie ses pages, les sélecteurs sont regroupés dans `scraping/linkedin.py` et couverts par des tests sur des fixtures.
- **PDF scannés** : non supportés (pas d'OCR). Fournir un PDF texte, un `.docx` ou un `.md`. L'onglet « Texte extrait » de la page CV montre exactement ce qui a été lu.
- **Analyse d'un CV** : 6 appels au LLM (~30 s pour un CV court, ~1 min 30 pour un CV dense avec `qwen2.5:7b`). Un modèle 7B peut encore mal rattacher un détail (ex. une technologie au mauvais projet) ; un modèle plus gros (`qwen2.5:14b`) améliore la précision.
- **PyMuPDF est sous licence AGPL** : sans conséquence pour un usage personnel ; pour un service ouvert au public, il faut publier le code source (ou acquérir une licence commerciale).
- **Performance** : un modèle 7B évalue une offre en ~10 s sur Apple Silicon (Ollama natif), beaucoup plus lentement en CPU dans Docker. Les recherches sont traitées une par une.
- **Un seul serveur web** : la file des recherches vit dans le processus Streamlit. Pour plusieurs instances derrière un load balancer, il faudrait un worker séparé (voir feuille de route).
- **Cookie de session** : il est posé par JavaScript, ce qui le rend lisible par un script de la page (pas `HttpOnly`). Ce risque est limité, car l'interface n'affiche aucun HTML fourni par l'utilisateur.

## Feuille de route

- [x] Interface web pour consulter le classement *(v0.2)*
- [ ] Migrations de schéma avec Alembic
- [ ] Worker de recherche séparé (file en base) pour passer à plusieurs instances web
- [ ] Autres sources d'offres (Welcome to the Jungle, France Travail API, Indeed…)
- [x] Avis sur les offres réutilisés par les recherches suivantes *(v0.5)*
- [ ] Suivi des candidatures (statut par offre : à postuler / postulé / entretien / refus)
- [ ] Génération d'une lettre de motivation ciblée pour les meilleures offres
- [ ] Notification (email / Telegram) du top hebdomadaire et recherches planifiées
- [ ] Réinitialisation du mot de passe par email

## Journal des évolutions

### 0.12.2 — 2026-11-15
- **Suivi de l'analyse du CV** : barre de progression par étape (Identité, Expériences, Formation, Projets, Compétences, Synthèse, Indexation), étapes en cours / terminées / en échec, **temps écoulé** et **temps restant estimé** à partir des durées des dernières analyses (colonne `cv_profiles.analysis_progress`, ajoutée automatiquement).
- **`LLM_TIMEOUT`** (900 s par défaut) : un appel au LLM bloqué (mémoire saturée…) échoue au lieu de faire attendre l'analyse indéfiniment.
- Menu latéral : modèles Ollama affichés sur deux lignes.

### 0.12.1 — 2026-11-15
- **CI/CD GitHub Actions** (`.github/workflows/ci-cd.yml`) : lint + tests à chaque push / PR ; sur `main`, image publiée sur GHCR puis déploiement automatique sur le VPS par SSH, avec contrôle de santé.
- `docker-compose.prod.yml` (image GHCR, ports PostgreSQL et Streamlit fermés au public) et `deploy/deploy.sh` (déploiement / retour arrière).
- Proxy HTTPS au choix : **Apache** existant (modèle `deploy/apache-getiajob.conf`, WebSocket compris, certificat par certbot) ou Caddy (profil `prod`, démarré par `deploy.sh` seulement s'il est actif).
- Formatage `ruff format` appliqué à `services/access.py` et `views/home.py`.
- Menu latéral : le pied de page affiche les modèles Ollama configurés (`LLM_MODEL`, `EMBEDDING_MODEL`).
- Déploiement : `VPS_PORT` / `VPS_PATH` acceptés en variable **ou** en secret, contrôle des secrets manquants et test de connexion SSH avant le déploiement (port et hôte affichés dans le log).

### 0.12.0 — 2026-11-15
- **Adhésion sur invitation** (`INVITE_ONLY=true` par défaut) : la création de compte exige un code d'invitation à usage unique, lié à l'adresse invitée et valable `INVITATION_DAYS` jours (le premier compte d'une base vide en est dispensé ; en SSO, une invitation pour l'adresse suffit).
- **Accueil** : bloc **« Accès sur invitation »** pour les visiteurs (parcours en 4 étapes + formulaire de demande), première étape de « Comment ça marche » adaptée.
- **Connexion** : champ *Code d'invitation*, lien d'invitation qui ouvre « Créer un compte » pré-rempli, formulaire de demande pour qui n'a pas d'invitation.
- **Admin › Utilisateurs › Demandes d'accès** : accepter / refuser, renvoyer / annuler une invitation, inviter directement une adresse ; administrateurs prévenus par e-mail à chaque demande.
- Nouvelle table `access_requests` (créée automatiquement) ; nouvelles variables `INVITE_ONLY`, `INVITATION_DAYS`, `PUBLIC_URL`.

### 0.11.1 — 2026-11-15
- **Accueil** : nouvel encart **« Tes alertes e-mail aussi »** expliquant en trois étapes comment récupérer les offres reçues par mail (alerte APEC → transfert, manuel ou automatique, vers l'adresse de la boîte GetIAJob → offres notées avec les autres), avec rappel d'envoyer depuis l'adresse de son compte. Affiché uniquement si la boîte IMAP est configurée ; adapté au mode sombre et au mobile.

### 0.11.0 — 2026-11-15
- **Admin › Utilisateurs › Consulter** : un administrateur peut se connecter « en tant que » un utilisateur pour voir ses données (tableau de bord, CV, fiche, avis, recherches, candidatures) **en lecture seule**, avec un bandeau et un bouton *Revenir à mon compte*.
- Garde-fou **lecture seule** au niveau de la base : toute requête de modification est refusée pendant une consultation (message clair à la place de l'action) ; contrôles qui enregistrent immédiatement désactivés (statut d'une candidature).
- Début et fin de consultation tracés dans le **journal d'audit**.
- Le complément de score des candidatures se fait à l'affichage, sans écriture en base.

### 0.10.1 — 2026-11-15
- **Mes candidatures** repensée :
  - date de candidature mise en évidence (pavé calendrier coloré selon le statut, ancienneté) ;
  - **score de l'offre** en anneau et synthèse de l'IA, conservés au moment de « J'ai postulé » (sinon meilleur score de l'offre parmi tes CV) ;
  - salaire, source, statut en pastille colorée ;
  - **frise d'avancement** Candidature → Entretiens → Offre → Décision ;
  - chiffres clés (taux de réponse, score moyen), encart **À relancer** (sans nouvelles depuis 10 jours) à côté de *À venir* ;
  - **tri** par date de candidature, score ou prochaine étape.
- Colonnes `salary`, `score`, `summary` ajoutées à `job_applications` (mise à niveau automatique).

### 0.10.0 — 2026-11-15
- **« J'ai postulé »** sur chaque offre de *Mes recherches* (date de candidature, commentaire) ; l'offre affiche ensuite « Postulé le … », qui ouvre directement son suivi.
- Nouvelle page **Mes candidatures** : statut (Postulé → Entretiens en cours → Offre reçue → Acceptée / Refusée / Abandonnée), prochaine étape datée, **journal des entretiens** (RH, manager, technique, téléphone, test, relance, retour, note) daté et commenté, encart *À venir*, filtres, candidatures saisies à la main pour les offres trouvées ailleurs.
- Les changements de statut sont consignés dans le journal ; le premier entretien fait passer une candidature « Postulé » en « Entretiens en cours ».
- Tableau de bord : carte **Candidatures en cours** avec la prochaine étape ; chiffres clés sur 3 colonnes.
- Les candidatures conservent une copie de l'offre (poste, entreprise, lien) et les offres concernées sont exclues de la purge.

### 0.9.0 — 2026-11-15
- **Admin › Supervision** : activité (recherches, taux d'échec, durée, utilisateurs actifs, comptes et CV, recherches par jour), file des recherches avec détection des **recherches bloquées**, boutons **Arrêter** / **Relancer**, derniers échecs filtrables, **santé du système** (PostgreSQL, Ollama, relève des mails, sauvegarde, IMAP à la demande, version), alertes et **journal d'audit**.
- **Admin › Qualité & IA** : stock d'offres (complétude, âge, doublons fusionnés, nouvelles offres par jour et par source), **purge** des offres anciennes, qualité du classement (répartition des notes, taux de 👍 par source, motifs de rejet), **consommation de l'IA** (appels, erreurs, durées moyenne et p95 par étape, taux de cache), **santé des sources** (erreurs HTTP, 429, temps de réponse, quota Adzuna).
- **Instrumentation** (table `metric_events`) : appels LLM et embeddings, cache des évaluations, requêtes HTTP de chaque source, collectes, doublons, relèves des mails, sauvegardes. L'enregistrement n'interrompt jamais un traitement.
- **Journal d'audit** (`audit_log`) : rôles, suppressions de comptes, recherches arrêtées / relancées, purges, imports de mails manuels, sauvegardes, notifications.
- **`getiajob monitor`** + `scripts/cron-monitor.sh` : détection automatique des problèmes (table `monitor_alerts`), **notification e-mail** des administrateurs à l'ouverture, au rappel et à la résolution d'une alerte (SMTP déduit de la boîte IMAP).
- **Sauvegarde** : `scripts/backup-db.sh` (pg_dump, rotation, suivi dans la supervision) ; `scripts/cron-run.sh` commun à toutes les tâches cron.
- Une recherche arrêtée par un administrateur n'est plus modifiée par le traitement qui se termine en arrière-plan.

### 0.8.3 — 2026-11-15
- **Réévaluation automatique** : quand le texte d'une offre déjà notée change (description complétée par une autre source ou récupérée après coup), son embedding est recalculé et sa note IA est réévaluée à la prochaine recherche qui la retient. L'ancienne note reste affichée d'ici là ; les autres offres gardent leur note en cache.

### 0.8.2 — 2026-11-14
- Nouvelle commande **`getiajob mail-sync`** (`--dry-run`, `--quiet`) pour relever les alertes e-mail hors recherche, avec code de sortie exploitable.
- Script **`scripts/cron-mail-sync.sh`** prêt pour cron : exécution dans le conteneur `web` (ou `app` éphémère), `PATH` adapté, journal `data/logs/mail-sync.log` horodaté (fuseau indiqué) et limité en taille.
- **Verrou PostgreSQL** : une seule relève à la fois (cron, recherche, bouton admin) ; les autres passent leur tour proprement.

### 0.8.1 — 2026-11-14
- Alertes e-mail : **les alertes importées sont supprimées de la boîte** après traitement (déplacées dans sa corbeille, ex. « [Gmail]/Corbeille », vidée automatiquement après 30 jours ; sans corbeille : marquées supprimées puis effacées). La suppression n'a lieu qu'après l'enregistrement des offres en base. Les mails non importés (expéditeur sans compte ou non authentifié, mail sans offre) restent dans la boîte pour vérification. Les alertes importées avant cette version sont aussi supprimées au prochain passage.
- Réglage `MAIL_DELETE_PROCESSED` (`true` par défaut ; `false` = lecture seule). L'*Aperçu de la boîte* (admin) ne supprime jamais rien.

### 0.8.0 — 2026-11-14
- **Nouvelle source APEC, via les alertes e-mail** :
  - chaque utilisateur transfère ses alertes APEC (à la main ou par règle automatique) vers la boîte commune de GetIAJob (ex. Gmail), **depuis l'adresse de son compte** ;
  - la boîte est relevée en **IMAP** à chaque recherche incluant l'APEC ; chaque mail n'est traité qu'une fois ;
  - extraction des offres (numéro, intitulé, entreprise, contrat, lieu, nom de l'alerte, date) à partir des liens de l'alerte, sans appel à apec.fr ; lien direct vers l'annonce ;
  - **rattachement à l'utilisateur expéditeur** (transfert manuel : `From` ; transfert automatique : `X-Forwarded-For`), uniquement si le serveur de réception l'a authentifié (SPF/DKIM/DMARC) ; expéditeur inconnu → mail ignoré ;
  - offres **privées** : visibles et notées uniquement pour leur propriétaire (filtrage dans le classement, dédoublonnage limité aux offres publiques ou à celles du même utilisateur) ; supprimées avec son compte.
- Fiche de recherche : consigne de transfert (adresse de la boîte et adresse du compte) quand l'APEC est disponible.
- **Admin › Sources d'annonces** : carte APEC, section *Alertes e-mail APEC* avec **Aperçu de la boîte** (analyse sans enregistrer), **Importer maintenant**, compteurs par statut et journal des derniers mails traités.
- Base : colonne `job_offers.owner_user_id`, table `mail_imports` (mise à niveau automatique).

### 0.7.2 — 2026-11-13
- France Travail : l'API « Offres d'emploi v2 » n'est plus disponible en libre-service sur francetravail.io (accès restreint, sur demande). Mention ajoutée sur la carte *Admin › Sources d'annonces* et dans ce README ; le connecteur est conservé pour les applications autorisées.

### 0.7.1 — 2026-11-13
- Nouvelle entrée **Admin › Sources d'annonces** :
  - une carte par site (LinkedIn, France Travail, Adzuna) : type d'accès, statut *Active* / *Clés manquantes*, variables `.env`, lien d'inscription, offres en base, offres vues sur 7 jours, dernière collecte ;
  - **Tester une recherche** : interroge une source en direct (mots-clés, lieu, fraîcheur, jusqu'à 30 offres), sans écriture en base ni appel à l'IA ; affiche durée, résultats détaillés ou erreur exacte.
- Messages d'erreur explicites quand France Travail ou Adzuna refusent les identifiants.
- Adzuna : salaires inférieurs à 1 000 € (taux horaires mal étiquetés) et catégorie « Unknown » ignorés.

### 0.7.0 — 2026-11-13
- **Nouvelles sources d'offres : France Travail et Adzuna**, en plus de LinkedIn, via leurs **API officielles** (aucun scraping) :
  - **France Travail** (API *Offres d'emploi v2*) : authentification OAuth2, conversion des lieux de la fiche (ville, département, région, code postal, « France ») ; description complète, type de contrat, expérience, salaire, compétences, offres de sites partenaires (lien vers l'annonce d'origine) ;
  - **Adzuna** (agrégateur) : salaire (réel ou estimé), contrat, temps de travail ; extrait de description ;
  - activées en renseignant leurs clés dans `.env` (`FRANCETRAVAIL_CLIENT_ID` / `_SECRET`, `ADZUNA_APP_ID` / `_KEY`) ; sans clé, la source est simplement ignorée ;
  - une source en panne n'interrompt pas la recherche.
- **Dédoublonnage entre sites** : une même annonce (intitulé + entreprise) vue sur plusieurs sources n'apparaît qu'une fois ; on garde la description la plus complète. Les offres LinkedIn déjà en base reçoivent leur empreinte au démarrage.
- **Choix des sources** : pastilles « Sources d'annonces » dans la **Fiche de recherche** (valeur par défaut) et dans **Nouvelle recherche** ; seules les sources configurées sur le serveur sont proposées.
- **Badge de provenance** (LinkedIn, France Travail, Adzuna) sur chaque offre des résultats.
- Base : colonnes `job_offers.source`, `job_offers.fingerprint` et `user_profiles.sources` (mise à niveau automatique).
- APEC étudiée mais non intégrée : le site bloque les robots (captcha) et n'offre pas d'API publique.

### 0.6.3 — 2026-11-13
- **Mon profil › CV : chaque bloc est modifiable** pour corriger une analyse erronée :
  - interrupteur **« Modifier l'identité »** (nom, titre, niveau, coordonnées, résumé, postes ciblés) et **« Modifier ce bloc »** sur chaque onglet ;
  - **Expériences** : une fiche par poste (intitulé, entreprise, lieu, dates, **type de contrat** en pastilles — ex. Freelance → CDI —, description, réalisations, technologies), ajout et suppression ;
  - **Formation** (+ certifications), **Projets** : mêmes fiches éditables ;
  - **Compétences** et **Langues** : tableaux éditables (ajout / suppression de lignes), savoir-être et centres d'intérêt en texte ;
  - après enregistrement : badge **« Corrigé manuellement »**, l'empreinte vectorielle du CV et les scores déjà calculés sont réinitialisés (recalculés à la prochaine recherche) ;
  - la **ré-analyse** d'un CV corrigé demande confirmation, car elle écrase les corrections.

### 0.6.2 — 2026-11-13
- Analyse du CV : une expérience **sans type de contrat précisé est considérée comme un CDI**, sauf si l'intitulé ou l'employeur indique un stage, une alternance, du freelance, de l'intérim ou un CDD. Règle appliquée dans le modèle (`schemas.Experience`) : elle vaut pour les nouvelles analyses comme pour les CV déjà analysés, sans ré-analyse.

### 0.6.1 — 2026-11-13
- **Tableau de bord** repensé :
  - **bandeau d'accueil** en dégradé : « Bonjour / Bonsoir », objectif, critères en pastilles, raccourcis vers *Nouvelle recherche*, *Mes recherches* et *Ma fiche* ;
  - **cinq chiffres clés** en cartes iconées : complétude de la fiche (anneau), CV et état de l'analyse, recherches (dont en cours), meilleur score, avis donnés ;
  - **prochaines étapes** avec barre de progression et une étape de plus (« Faire analyser mon CV »), étapes faites cochées et barrées ;
  - **meilleures offres** de la dernière recherche avec score en anneau et lien direct, ou un encart d'invitation s'il n'y a pas encore de recherche ;
  - **dernières recherches** avec badge de statut, nombre d'offres collectées et meilleur score ;
  - bandeau d'information quand une recherche est en cours. Compatible mode sombre.

### 0.6.0 — 2026-11-13
- **Rôle administrateur** sur les comptes (`users.is_admin`). Migration : à l'ajout de la colonne, les comptes déjà existants deviennent administrateurs ; ensuite, seul le premier compte d'une base vide l'est automatiquement.
- Nouvelle entrée de menu **Admin › Utilisateurs** (visible des seuls administrateurs) : liste des comptes avec statistiques, recherche, filtre par rôle, donner / retirer le rôle admin, suppression d'un compte avec ses données. Garde-fous : pas de modification de son propre rôle ni de suppression de son propre compte ; actions revérifiées côté serveur.
- Nouveau service `services/admin.py` et tests associés.

### 0.5.10 — 2026-11-13
- Le **menu de gauche est déplié à chaque connexion** (formulaire, SSO ou reconnexion automatique par cookie). Streamlit conservait l'état replié pendant toute la session du navigateur : un menu fermé avant une déconnexion restait fermé à la reconnexion. Pas de dépliage forcé sur mobile (largeur < 768 px), où il recouvrirait la page.

### 0.5.9 — 2026-11-13
- **Mon profil › Fiche de recherche** repensée :
  - récapitulatif en tête : jauge de complétude colorée, critères actuels en pastilles iconées, éléments manquants en pastilles orange en pointillés ;
  - formulaire découpé en quatre blocs (*Le poste*, *Les conditions*, *Ce qui compte pour moi*, *Exclusions*), chacun avec une icône et un sous-titre ;
  - mode de travail, type de contrat et niveau d'expérience en **pastilles cliquables** au lieu de listes déroulantes ;
  - bouton *Enregistrer ma fiche* pleine largeur. Compatible mode sombre.

### 0.5.8 — 2026-11-13
- **Mon profil › CV** : tous les onglets repensés dans le style de la frise des expériences.
  - **Carte d'identité** du profil : avatar à initiales, nom, titre, niveau, années d'expérience, coordonnées iconées (email, LinkedIn, GitHub, site cliquables), résumé et postes ciblés.
  - **Formation** : frise des diplômes (spécialité, établissement, lieu, période, détails) et certifications en cartes.
  - **Projets** : grille de cartes avec bouton *Voir* si un lien existe, description et technologies.
  - **Compétences** : une carte par catégorie avec compteur, disposition en colonnes ; les catégories en double renvoyées par l'IA sont fusionnées (compteur de l'onglet ajusté) ; savoir-être dans une carte distincte.
  - **Langues & intérêts** : une carte par langue avec jauge selon le niveau (A1 → natif), centres d'intérêt en pastilles.
  - **Texte extrait** : sections détectées en pastilles.
  - Compatible mode sombre.

### 0.5.7 — 2026-11-13
- **Mon profil › CV › Expériences** repensé en **frise chronologique** : chiffres clés (postes, entreprises, années d'expérience, technologies citées), une carte par poste avec intitulé, entreprise et lieu iconés, période et durée à droite, type de contrat et badge « En poste », description, réalisations cochées, technologies en pastilles. Compatible mode sombre.

### 0.5.6 — 2026-11-13
- **Bouton sombre / clair** dans le header (connecté ou non), icône lune / soleil dans un cercle translucide sans contour : bascule entre affichage sombre et clair, choix mémorisé dans le navigateur pour toutes les pages.
- Thèmes clair et sombre déclarés dans `.streamlit/config.toml` (couleur principale indigo, éclaircie en sombre) ; accueil, cartes d'offres, pastilles, encarts et onglets adaptés au mode sombre.

### 0.5.5 — 2026-11-13
- **Mes recherches** repensée : en-tête de recherche avec statut et critères en pastilles, chiffres clés en cartes (dont meilleur score et score moyen), filtre par pertinence, cartes d'offres avec score en anneau, métadonnées iconées (entreprise, lieu, « publiée il y a N j », salaire), compétences courtes uniquement (les phrases longues renvoyées par l'IA passent dans l'analyse), *Analyse détaillée* en encarts colorés avec décomposition du score, actions regroupées (*Voir l'offre*, *J'aime*, *Pas pour moi*).
- Les textes issus des offres sont échappés avant affichage HTML.

### 0.5.4 — 2026-11-13
- Onglets du CV (Expériences, Formation, Projets, Compétences, Langues & intérêts, Texte extrait) plus visibles : pastilles avec icône, onglet actif rempli en dégradé violet, survol mis en évidence.
- Boutons Télécharger / Ré-analyser / Supprimer d'un CV regroupés côte à côte, alignés à droite (ils étaient répartis sur trois colonnes).

### 0.5.3 — 2026-11-13
- **Page d'accueil redessinée** : hero sur fond dégradé avec titre accrocheur et aperçu illustratif d'un « top de la semaine » (scores en anneaux), chiffres clés, cartes de fonctionnalités animées au survol (dont les avis sur les offres), étapes en frise numérotée, encart « Tes données restent chez toi », bandeau d'appel à l'action. Icônes Lucide, mise en page adaptée au mobile.

### 0.5.2 — 2026-11-13
- Header : bouton **Se connecter / S'inscrire** (blanc, bien visible) quand on n'est pas connecté ; masqué sur la page de connexion.
- Page d'accueil sans menu latéral ; plus de menu latéral du tout tant qu'on n'est pas connecté.
- Boutons « Se connecter » / « Créer un compte » retirés du corps de la page d'accueil (redondants avec le header).

### 0.5.1 — 2026-11-13
- Header : le logo devient l'icône Lucide *briefcase-business*, suivie du nom du site **GetIAJob** (cliquables, retour à l'accueil), puis du nom de la page séparé par un trait. Titre de l'onglet du navigateur : « GetIAJob ».

### 0.5.0 — 2026-11-13
- **Avis sur les offres** : boutons « J'aime » / « Pas pour moi » sous chaque résultat, motifs de refus à cocher, commentaire libre, option d'exclusion de l'entreprise.
- Les avis affinent les recherches suivantes sans alourdir le calcul : exclusion des offres écartées, **ajustement du score par similarité** avec les offres aimées / écartées (embeddings existants, appliqué dès la pré-sélection), **résumé compact transmis à l'IA** (désactivable).
- Bouton **Réajuster avec mes avis** : re-trie les offres déjà collectées avec les avis actuels, sans interroger LinkedIn.
- Onglet **Mes avis** dans Mon profil : liste, suppression, aperçu de « ce que l'IA retiendra ».
- Résultats : ajustement affiché sur chaque offre concernée, offres écartées masquables.
- Base : nouvelle table `job_feedback` (créée automatiquement au démarrage).

### 0.4.1 — 2026-11-13
- **Header pleine largeur**, du bord gauche au bord droit de la fenêtre (au-dessus du menu latéral), toujours visible.
- Le nom « getiajob » est remplacé par le **nom de la page courante**.
- **Logo cliquable** (mallette) pour revenir à l'accueil : l'entrée « Accueil » disparaît du menu une fois connecté.
- **Menu utilisateur** en haut à droite (nom + menu déroulant) avec *Mon compte* et *Se déconnecter* ; ces entrées quittent le menu latéral.
- Nouvelle rubrique **Mes ressources** regroupant *Tableau de bord* et *Mon profil*.
- Correctifs : titre de la page aligné verticalement avec le logo ; le bouton de réouverture du menu latéral (masqué par erreur avec la barre d'outils native) est de nouveau visible sous le header.

### 0.4.0 — 2026-11-13
- **Analyse du CV en arrière-plan** : elle tourne dans un thread du serveur et continue quand on change de page, d'onglet ou de fenêtre. Statut en base (`pending` / `running` / `done` / `failed`), suivi en direct sur la carte du CV et sur le tableau de bord, bouton « Réessayer » en cas d'échec, reprise automatique au redémarrage du serveur.
- Nouvelle file de tâches de fond `BackgroundRunner` (recherches et analyses de CV dans deux files séparées).
- **Menu simplifié** : une seule entrée **Mon profil**, avec dans la zone centrale un sélecteur **CV** / **Fiche de recherche**.
- **Header collé en haut de la page**, toujours visible au défilement.
- **Icônes professionnelles** : Material Symbols dans le menu, les statuts et les notifications ; logo SVG dans le header ; plus aucun emoji.
- La « date de prise de poste » est remplacée par la **fraîcheur des annonces** (24 h, 3 jours, 1 semaine, 2 semaines, 3 semaines, 1 mois), reprise par défaut dans chaque nouvelle recherche.
- Base : colonnes `cv_profiles.analysis_status`, `analysis_error`, `analysis_started_at` et `user_profiles.max_offer_age_days` ajoutées ; `user_profiles.start_date` supprimée (mise à niveau automatique au démarrage).

### 0.3.0 — 2026-11-13
- **Extraction du CV refondue** :
  - lecture des PDF selon la mise en page avec PyMuPDF (détection des colonnes et des fragments de ligne, ordre de lecture rétabli, titres à lettres espacées et titres sur deux lignes normalisés, sections balisées « ## ») ;
  - DOCX lus avec leurs tableaux et zones de texte.
- **Analyse par sections** : cinq extractions LLM ciblées en parallèle, puis une synthèse. Le schéma du profil est enrichi : coordonnées, expériences (dates, durée, type de contrat, mission, réalisations, technologies), formation (diplôme, établissement, spécialité, dates, détails), projets, compétences par catégorie, savoir-être, langues avec niveau, certifications, centres d'intérêt.
- **Garde-fous anti-hallucination** :
  - dates et valeurs bidon (« Non spécifié ») supprimées ;
  - compétences, langues et intérêts absents du CV retirés ;
  - coordonnées complétées par expressions régulières.
- Page **Mon CV** en onglets (dont « Texte extrait »).
- Profils v1 toujours lisibles, signalés « à ré-analyser » puis ré-analysés automatiquement à la recherche suivante ; les scores en cache du CV sont alors invalidés.

### 0.2.0 — 2026-11-13
- **Interface web Streamlit**, déployable en local et en ligne : page d'accueil, espace connecté avec header, menu latéral et zone centrale.
- **Comptes utilisateurs** : inscription/connexion (bcrypt), « rester connecté » par cookie + jeton haché en base, SSO OpenID Connect optionnel, page « Mon compte », `ALLOW_SIGNUP` pour fermer les inscriptions.
- **Fiche utilisateur** : poste recherché, lieux, date de prise de poste, télétravail, contrat, niveau, salaire, attentes, exclusions. Ces informations alimentent la génération des requêtes et l'évaluation des offres.
- **CV en base** (fichier original inclus) : import, analyse, téléchargement, ré-analyse, suppression.
- **Sessions de recherche personnalisées** : date de publication minimale, nombre de résultats, ciblage et options avancées ; exécution en tâche de fond avec suivi en direct ; historique et résultats figés ; export Markdown.
- Pipeline : préférences passées dans l'état du graphe, tri limité aux offres de la recherche, poste de la fiche toujours en tête des requêtes.
- Base : tables `users`, `auth_sessions`, `user_profiles`, `search_sessions` ; CV rattachés aux utilisateurs ; mise à niveau automatique des bases v0.1.
- Docker : service `web` (port 8501), profil `prod` avec Caddy (HTTPS automatique) ; devcontainer avec redirection du port 8501 ; commande `getiajob web`.

### 0.1.0 — 2026-11-13
- Création du projet : pipeline LangGraph à trois agents (analyse CV → collecte LinkedIn → tri).
- Modèles open source via Ollama (`qwen2.5:7b`, `nomic-embed-text`).
- Persistance PostgreSQL + pgvector (CV, offres, scores), cache des analyses et évaluations.
- CLI `getiajob` (`init-db`, `doctor`, `pull-models`, `analyze-cv`, `run`, `top`), rapport Markdown.
- Docker Compose (db, ollama optionnel, app) et devcontainer VS Code.
- Tests unitaires du parseur LinkedIn, des filtres et du graphe.
