# Studio Réseaux — SURIN / GRIGUER · PARIS · BÂTONNAT 2028

Application web de community management de la campagne de Clarisse Surin et
Merav Griguer. L'équipe s'en sert depuis un navigateur, sur PC comme sur Mac, sans
rien installer. Elle couvre tout le circuit : un contenu est créé, décliné
automatiquement pour chaque réseau, validé, programmé au meilleur moment, puis
publié. Ses performances sont ensuite renvoyées en CSV vers l'outil de campagne.

> Même socle technique que l'appli Radio J (Python · Flask · HTMX · Claude).
> Cette fois, c'est une **application web hébergée** et partagée par toute l'équipe.

## Le circuit de travail

1. **Créer** (`+ Nouveau contenu`) :
   - choisir le type : **photo(s)**, **vidéo** ou **texte → carrousel** ;
   - choisir les **réseaux** : Instagram (publication, carrousel, reel, story),
     Facebook, LinkedIn, X, Threads, TikTok et YouTube Shorts ;
   - pour une vidéo, l'appli demande s'il faut **monter une séquence** : non
     (vidéo entière), oui en indiquant début et fin, ou **montage automatique
     par l'IA**, qui choisit le meilleur passage d'après la transcription ;
   - ajouter un brief, les **comptes collaborateurs Instagram** et les comptes à identifier.
2. **Préparation automatique** :
   - **formats** par réseau : 9:16, 4:5, 1:1 ou 16:9, avec fond flouté, aplat
     anthracite ou recadrage. La vidéo est coupée à la durée maximale de chaque réseau ;
   - **sous-titres** : transcription sur le serveur, corrigée par Claude
     (noms propres du glossaire, ponctuation), puis incrustée aux couleurs de la
     charte, avec les chiffres et les mots clés en bronze. Un fichier `.srt` est fourni ;
   - **carrousels** : Claude découpe le texte en diapositives, rendues selon la
     charte (couverture, contenu, chiffre, citation, clôture « ÉCRIVONS / ENSEMBLE /
     LE BARREAU DE DEMAIN »). Sur LinkedIn, le carrousel part en **document PDF** ;
   - **signature de campagne** incrustée : SURIN / GRIGUER · PARIS · BÂTONNAT 2028 ;
   - **légendes, hashtags, premier commentaire et texte alternatif**, adaptés à
     chaque réseau, dans le respect de la **déontologie** de la profession et des
     élections ordinales (règles inscrites dans le prompt de l'IA).
3. **Relire et valider** : chaque déclinaison peut être modifiée. Le compteur de
   caractères suit la limite du réseau. La validation par un « validateur », par
   exemple les candidates, peut être rendue obligatoire.
4. **Programmer au meilleur moment** : l'appli propose des créneaux adaptés à
   une audience d'avocats parisiens. Ces créneaux sont ensuite **appris de vos
   propres performances** (marqués ★).
5. **Publier** :
   - **automatiquement** quand le connecteur du réseau est configuré : Instagram
     (y compris en **collaboration** et avec identifications), Facebook,
     LinkedIn et X ;
   - en mode **assisté** sinon (TikTok, YouTube, Threads, ou jetons absents). À
     l'heure prévue, l'équipe est notifiée. Elle trouve le **pack** (médias et
     `legende.txt`), un bouton « copier la légende » et un lien vers le réseau.
     Il suffit ensuite de coller le lien du post publié.
6. **Mesurer** :
   - les statistiques Instagram et Facebook remontent seules chaque matin ;
   - **import CSV** des exports natifs (Meta Business Suite, LinkedIn, X,
     TikTok…), en français ou en anglais, avec « ; » ou « , » ;
   - **export CSV** pour l'outil de campagne : date, réseau, thème, lien,
     impressions, couverture, interactions et taux d'engagement. Le fichier
     s'ouvre directement dans Excel (version française).

## Charte graphique intégrée

| Élément | Valeur |
|---|---|
| Fond principal anthracite | `#393736` |
| Fond clair / ivoire | `#EEEAE3` |
| Accent bronze (mots importants, dates, chiffres, filets) | `#BF9C75` |
| Texte secondaire clair | `#C3B9AD` |
| Typographie | Montserrat Regular 400 / Bold 700 (fournie, licence OFL) |
| Signature | SURIN / GRIGUER — PARIS · BÂTONNAT 2028 |
| Accroche | PARIS. 2028. ENSEMBLE. |

La charte n'utilise ni dégradé ni ombre portée, et aucune couleur en dehors de
la palette. Les titres courts sont en capitales et les signatures en lettres
espacées. Tout cela est réglable dans **Réglages → Charte**, avec un aperçu en direct.

## Rôles

- **Administrateur** : tout faire, y compris les réglages et la gestion de l'équipe.
- **Validateur** : relire et valider les déclinaisons.
- **Éditeur** : créer, préparer et programmer. La publication attend une
  validation si celle-ci est obligatoire.

## Installation sur un serveur (recommandé)

Un petit serveur suffit (2 processeurs et 4 Go de mémoire), avec Docker installé
et une adresse HTTPS : OVH, Scaleway, Render, Railway, Fly.io…

```bash
cp .env.example .env        # puis remplir SECRET_KEY, ANTHROPIC_API_KEY, PUBLIC_BASE_URL
docker compose up -d --build
```

Ouvrez ensuite l'adresse du serveur. Le premier écran crée le compte
administrateur. Ajoutez l'équipe dans **Réglages → Équipe**.

Toutes les données (base SQLite, fichiers) se trouvent dans `./data`. Pour
sauvegarder, il suffit de copier ce dossier.

### Brancher les réseaux (publication automatique)

| Réseau | Variables `.env` | Prérequis |
|---|---|---|
| Instagram | `META_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` | Compte pro relié à une Page, appli Meta (instagram_content_publish, instagram_manage_insights) |
| Facebook | `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_TOKEN` | pages_manage_posts, pages_read_engagement |
| LinkedIn | `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN` | w_member_social (profil) ou w_organization_social (page) |
| X | `X_USER_ACCESS_TOKEN` | Accès API payant, OAuth 2.0 (tweet.write, media.write) |
| TikTok, YouTube, Threads | — | Publication assistée (pack et notification) |

Meta va chercher les médias à l'adresse `PUBLIC_BASE_URL`, via des liens à jeton
impossibles à deviner. L'appli doit donc être en ligne en HTTPS.

## Tester sur son ordinateur

- **Mac** : double-cliquer sur `lancer-mac.command`.
- **Windows** : double-cliquer sur `lancer-windows.bat`.

Il faut Python 3.11 ou plus récent et ffmpeg (`brew install ffmpeg` sur Mac,
`winget install ffmpeg` sous Windows).

## Développement

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
FLASK_DEBUG=1 python run.py          # http://localhost:8000
pytest -q
```

```
app/
  __init__.py      fabrique Flask, filtres, planificateur
  config.py        configuration (.env)
  db.py            SQLite : users, posts, assets, variants, metrics, settings
  auth.py          comptes et rôles
  brand.py         charte graphique et contexte de campagne par défaut
  networks.py      formats, limites, créneaux par réseau
  media.py         ffmpeg et Pillow : recadrage, sous-titres, carrousels, signature
  transcribe.py    transcription (faster-whisper), SRT
  ai.py            Claude : légendes, carrousels, sous-titres, montage automatique
  pipeline.py      chaîne automatique (arrière-plan)
  publishing.py    publication, planificateur, statistiques
  publishers/      connecteurs Meta, LinkedIn, X
  analytics.py     import et export CSV, créneaux appris, carte des créneaux
  views.py         pages
```

---

Conception et accompagnement IA : **Stéphane Zibi** — 06 75 84 04 58 —
[stephane@biziness.fr](mailto:stephane@biziness.fr)
