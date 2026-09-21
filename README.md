# ReviewRadar

Agregator produktów (elektronika, kosmetyki, produkty użytkowe/dom) z opiniami
zbieranymi z Reddita (oficjalne API przez PRAW), z wybranych forów
tematycznych dopasowanych do kategorii produktu, i z oficjalnych API
(Google Places, YouTube Data API) — bez web scrapingu. Opinie są
klasyfikowane pod względem sentymentu, a dla każdego produktu generowane
jest zbiorcze podsumowanie zalet/wad przy pomocy Claude (Anthropic API) — z
promptem dostosowanym do kategorii produktu.

## Apps

- **accounts** — tożsamość użytkownika (wbudowany `django.contrib.auth.User`,
  bez custom user modelu): rejestracja/logowanie/wylogowanie, `Watchlist`
  (obserwowane produkty) — patrz "Konta użytkowników" niżej
- **products** — `Category` (`main_category` + `spec_schema` — patrz
  "Kategorie produktów" niżej), `Product` (specyfikacja jako JSON
  zwalidowana względem schematu kategorii, pola `pros_summary`/`cons_summary`
  generowane przez `analysis`)
- **reviews** — `ReviewSource` (`source_type`: reddit/forum/review_site/inne,
  `main_categories` — dla jakich kategorii źródło jest właściwe,
  `parser_config` — subreddity/selektory CSS/parametry API,
  `tos_checked_at`/`tos_notes` — ślad weryfikacji ToS lub limitów API),
  `Review` (deduplikacja po sha256 URL-a źródłowego), `UserReview` (własna
  ocena 1-5 + komentarz zalogowanego użytkownika, edytowalna), `ScrapeJob`
  (log zadań pobierania)
- **scrapers** — logika pobierania: `reddit_scraper.py` (PRAW),
  `generic_forum_scraper.py` (BeautifulSoup, selektory CSS z
  `ReviewSource.parser_config` — nowe forum tego samego silnika bez nowego
  pliku), `forum_scraper_example.py` i `beauty_forum_scraper.py` (przykłady
  parserów "bespoke" dla konkretnego forum), `google_places_scraper.py` i
  `youtube_scraper.py` (oficjalne REST API, bez scrapingu HTML — ten sam
  kontrakt `fetch()` co parsery forów), `dedup.py` (wspólna warstwa
  zapisu z deduplikacją, identyczna niezależnie od źródła), `registry.py`
  (mapowanie `ReviewSource.parser_key` → parser), `tasks.py` (Celery tasks +
  `queue_fetch()` — dispatch po `source_type`, tylko dla źródeł pasujących do
  kategorii produktu)
- **analysis** — `sentiment.py` (klasyfikacja pojedynczej opinii),
  `summarizer.py` (zbiorcze zalety/wady), `prompts.py` (etykiety/kontekst
  kategorii wstrzykiwane do promptów Claude), Celery task w `tasks.py`,
  `APIUsageLog` (log zużycia API zewnętrznych — patrz "Monitoring kosztów"
  niżej), `SummaryFlag` (zgłoszenie nietrafnego podsumowania AI przez
  użytkownika)

## Kategorie produktów

`Category.main_category` to jedna z trzech głównych grup (`electronics`,
`cosmetics`, `household`), z których każda ma inny domyślny schemat
specyfikacji (`Category.spec_schema`, JSON Schema) — patrz
`products/spec_schemas.py`. `Product.specification` jest walidowany względem
schematu swojej kategorii (`Product.clean()`); wszystkie domyślne schematy są
permisywne (brak `required`, `additionalProperties: true`), więc nie psują
istniejących/ręcznie wpisanych danych.

**Dodanie nowej konkretnej kategorii** (np. "Perfumy" pod kosmetykami) — bez
zmian w kodzie:
1. W adminie (`/admin/products/category/add/`) utwórz `Category`, wybierz
   `main_category`.
2. Zostaw `spec_schema` puste, żeby dostało domyślny szablon dla wybranej
   `main_category` (`Category.save()`), albo wklej własny JSON Schema, jeśli
   ta konkretna kategoria potrzebuje dodatkowych pól (np. `screen_size_inches`
   dla laptopów).

**Dodanie nowej głównej kategorii** (czwartej grupy obok
elektroniki/kosmetyków/domu) wymaga zmiany w kodzie: nowa wartość w
`Category.MainCategory`, domyślny schemat w `products/spec_schemas.py`, i
zwykle też etykieta/aspekty w `analysis/prompts.py` (patrz "Analiza
sentymentu i podsumowań" niżej) — to świadomie rzadka, architektoniczna
zmiana, w przeciwieństwie do dodania kolejnej konkretnej kategorii.

## Wymagania

- Python 3.12
- Docker (dla Postgres + Redis) — opcjonalnie, patrz "Baza danych" niżej
- Konto Reddit z zarejestrowaną aplikacją (https://www.reddit.com/prefs/apps)
  jeśli chcesz realnie pobierać dane
- Klucz API Anthropic (https://console.anthropic.com/) jeśli chcesz realnie
  generować sentyment/podsumowania — jeśli to klucz "identity-linked"
  (workspace/SSO), API odrzuci zapytania błędem `anthropic-workspace-id is
  required` dopóki nie ustawisz też `ANTHROPIC_WORKSPACE_ID` w `.env`
  (identyfikator workspace'a z konsoli Anthropic)

## Instalacja lokalna

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt

copy .env.example .env
# uzupełnij .env: DJANGO_SECRET_KEY, REDDIT_*, ANTHROPIC_API_KEY wg potrzeb
```

## Baza danych

Domyślnie (`reviewradar.settings.dev`, ustawione w `manage.py`) projekt używa
lokalnego pliku SQLite — można od razu odpalić `runserver` bez stawiania
żadnej bazy. Do pracy nad Celery i tak potrzebny jest Redis, więc wygodnie
jest odpalić oba serwisy (Postgres + Redis) z Dockera, nawet jeśli Postgres
na starcie nie jest jeszcze używany:

```bash
docker compose up -d
```

Żeby faktycznie przełączyć aplikację na Postgres (np. do testów zbliżonych do
produkcji), ustaw `DJANGO_SETTINGS_MODULE=reviewradar.settings.prod` oraz
`DATABASE_URL` w `.env` (patrz `.env.example`) i uruchom migracje ponownie.

## Migracje i superuser

```bash
python manage.py migrate
python manage.py createsuperuser
```

Migracja `scrapers.0001_periodic_refresh_schedule` automatycznie tworzy w
`django-celery-beat` codzienne zadanie `refresh_recent_products`
(`Product`y dodane w ciągu ostatnich `REVIEW_REFRESH_WINDOW_DAYS` dni, tylko
dla `ReviewSource` pasujących do kategorii danego produktu).

## Uruchomienie

W czterech osobnych terminalach (przy aktywnym venv):

```bash
# 1. Serwer Django
python manage.py runserver

# 2. Redis + Postgres (jeśli jeszcze nie uruchomione)
docker compose up -d

# 3. Celery worker
celery -A reviewradar worker -l info --pool=solo   # --pool=solo wymagane na Windows

# 4. Celery beat (cykliczne odświeżanie opinii)
celery -A reviewradar beat -l info
```

Wyszukiwarka produktów: http://localhost:8000/
Panel admina: http://localhost:8000/admin/
REST API: http://localhost:8000/api/products/, http://localhost:8000/api/reviews/

## Wyszukiwarka produktów (frontend)

Prosty interfejs webowy (Django templates, bez JS) do przeglądania bazy:

- `GET /` — wyszukiwarka: filtrowanie po tekście (`?q=`, dopasowanie do
  nazwy/marki/modelu/kategorii), po kategorii głównej (`?main_category=`) i po
  konkretnej kategorii (`?category=<slug>`)
- `GET /products/{id}/` — szczegóły produktu: specyfikacja, zalety/wady
  (`pros_summary`/`cons_summary`, z przyciskiem "Zgłoś nietrafne podsumowanie"
  dla zalogowanych — patrz niżej), średnia ocena użytkowników w nagłówku, oraz
  cztery zakładki (CSS-only, bez JS): komentarze z YouTube, opinie z Google
  Places (obie filtrowane po `Review.source.parser_key`), oceny użytkowników
  (patrz niżej), i bezpośrednie zapytanie do Claude (`ai_overview` —
  generowane automatycznie, raz na produkt, przy pierwszym wejściu na stronę)

Widoki: `products/web_views.py`, routing: `products/web_urls.py` (namespace
`products`), szablony: `templates/base.html`,
`templates/products/{search,detail}.html`. Niezależne od REST API pod
`/api/` — korzysta bezpośrednio z ORM.

**Wyszukiwanie na żywo odpytuje YouTube/Google Places — ale za bramką
kosztową.** Wyszukiwanie w istniejącym katalogu jest zawsze darmowe i w
pełni publiczne, bez żadnych ograniczeń. Natomiast niepusty `?q=` **też**
kolejkuje (Celery) pobranie świeżych opinii z YouTube i Google Places dla
pierwszych `MAX_LIVE_FETCH_PRODUCTS` (domyślnie 5) pasujących produktów
(`scrapers/tasks.py::queue_live_search_fetch()`) — a to już kosztuje, więc
`product_search()` przepuszcza tę ścieżkę przez `products/rate_limit.py::cost_path_allowed()`:
- honeypot — ukryte pole `website` w formularzu (`templates/base.html`);
  wypełnione = bot, ścieżka kosztowa cicho wyłączona (reszta strony działa
  normalnie, żeby nie zdradzić botowi że został wykryty),
- limit godzinowy per IP (`SEARCH_COST_PATH_IP_RATE_PER_HOUR`, domyślnie 5/h),
- globalny limit dzienny (`DAILY_NEW_PRODUCT_LIMIT`, domyślnie 200/dzień) —
  oba liczone w cache (Redis w produkcji, `cache.incr()` z TTL, fixed window).

Po przekroczeniu limitu wyszukiwanie nadal zwraca wyniki z bazy — po prostu
bez tworzenia nowych wpisów i bez live-fetch (`cost_path_limited` w
kontekście szablonu). Reddit, fora i Crawlbase **nie** są objęte tą ścieżką:
Reddit/fora zostają za `refresh-reviews` (wymaga uprawnień operatora, patrz
REST API niżej), a Crawlbase strukturalnie nie pasuje (potrzebuje
konkretnego URL-a, nie szuka po nazwie produktu) — zostaje przy
`fetch-crawlbase-reddit-post/`. Błąd kolejkowania (np. broker Celery
niedostępny) jest logowany i połykany, nie wywala strony wyszukiwania.

**Wyszukanie produktu, którego nie ma w katalogu, automatycznie go tworzy —
też za tą samą bramką kosztową, plus dodatkowa walidacja `q`.** Skoro
`Review` wymaga FK do `Product`, żeby cokolwiek pobrać z API trzeba mieć
czego zapytanie dotyczy — jeśli `?q=` nie pasuje do niczego w bazie, jest
sensowne (`_is_meaningful_query()`: min. 3 znaki, nie sam powtórzony znak —
prosty filtr na oczywisty śmieć, nie realna detekcja spamu), ścieżka
kosztowa nie jest zablokowana, i wybrana jest przynajmniej kategoria główna
(`?main_category=`) lub konkretna (`?category=`) — `product_search()`
tworzy minimalny `Product` (marka/model to zgrubny podział wpisanej frazy,
patrz `_get_or_create_stub_product()`) w kategorii "Inne (<Główna
kategoria>)" i od razu odpala dla niego `queue_live_search_fetch()`. **Bez
wybranej kategorii auto-tworzenie się nie uruchamia** — nie ma jak sensownie
zgadnąć kategorii z samej frazy — strona pokazuje wtedy podpowiedź, żeby
wybrać kategorię.

## Konta użytkowników

Rejestracja (`/accounts/register/`) i logowanie/wylogowanie
(`/accounts/login/`, `/accounts/logout/` — Django's `LoginView`/`LogoutView`,
wylogowanie tylko przez `POST`) korzystają z wbudowanego modelu
`django.contrib.auth.User` — brak custom user modelu, brak systemu kont poza
tym (staff/superuser zostają osobnym, adminowym poziomem uprawnień, patrz
REST API niżej). Po zalogowaniu, na stronie produktu (`/products/{id}/`)
dostępne są trzy dodatkowe akcje (wszystkie `POST`, wymagają zalogowania,
przekierowują z powrotem na stronę produktu):

- **Obserwowanie produktu** — przycisk "Obserwuj"/"Obserwowane"
  (`accounts.models.Watchlist`, `unique_together=("user","product")`, toggle
  jednym requestem); lista własnych obserwowanych produktów pod
  `/accounts/watchlist/`.
- **Własna ocena 1-5 + komentarz** — zakładka "Oceny użytkowników" na stronie
  produktu (`reviews.models.UserReview`, osobny model od zescrapowanego
  `Review` — bez `ReviewSource`/sentymentu, edytowalny: kolejny `POST` tego
  samego użytkownika nadpisuje jego wcześniejszą ocenę zamiast tworzyć
  duplikat). Średnia ocena i liczba głosów pokazują się w nagłówku strony
  produktu.
- **Zgłoszenie nietrafnego podsumowania AI** — mały przycisk pod
  `pros_summary`/`cons_summary` (`analysis.models.SummaryFlag`,
  `unique_together=("product","user","target")` — powtórne zgłoszenie tego
  samego jest no-opem). Zgłoszenia widoczne wyłącznie w
  `/admin/analysis/summaryflag/` — na tym etapie bez osobnego workflow
  moderacji.

Wszystkie trzy akcje żyją w `products/web_views.py`
(`toggle_watchlist`/`submit_user_review`/`flag_summary`), mimo że dotyczą
modeli z innych aplikacji (`accounts`/`reviews`/`analysis`) — to kontynuacja
wzorca, w którym strona produktu jest jedną warstwą orkiestrującą, nie tylko
CRUD-em na `Product`.

**Pełny cykl życia konta:**

- **Profil** (`/accounts/profile/`) — nazwa użytkownika, e-mail, data
  dołączenia, liczba obserwowanych produktów i wystawionych ocen; linki do
  zmiany hasła/e-maila.
- **Zmiana hasła** (`/accounts/password-change/`) — wbudowany
  `PasswordChangeView`/`PasswordChangeDoneView`; sesja **nie** wylogowuje po
  zmianie (`update_session_auth_hash`, wbudowane w widok Django).
- **Reset hasła przez e-mail** (`/accounts/password-reset/` →
  `password-reset/done/` → link z e-maila → `reset/<uidb64>/<token>/` →
  `reset/done/`) — wbudowane `PasswordResetView`/`...ConfirmView`/itd.,
  tylko `template_name`/`success_url` skonfigurowane w `accounts/urls.py`.
  Treść e-maila w `templates/accounts/password_reset_email.html`. Lokalnie
  e-mail leci na `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`
  (domyślne, wypisuje się w terminalu `runserver`) — w produkcji ustaw SMTP
  przez zmienne `.env` (`EMAIL_HOST`/`EMAIL_HOST_USER`/... — patrz
  `.env.example`).
- **Zmiana e-maila** (`/accounts/change-email/`) — własny widok (Django nie
  ma odpowiednika), bez ponownego podania hasła (sesja już potwierdza
  tożsamość); e-mail musi być unikalny (`EmailChangeForm.clean_email`,
  wyklucza samego siebie) — reset hasła po e-mailu wymaga jednoznacznego
  mapowania e-mail→konto.
- **Throttling rejestracji** — `register()` sprawdza limit per-IP/godzinę
  (`REGISTER_IP_RATE_PER_HOUR`, domyślnie 5) **przed** walidacją formularza,
  tym samym prymitywem co bramka kosztowa wyszukiwarki
  (`products/rate_limit.py::increment_and_check` — upublicznione z
  wcześniejszego `_increment_and_check`, reużywane międzyaplikacyjnie zamiast
  duplikować licznik fixed-window). Przekroczenie limitu → komunikat błędu,
  żadne konto nie powstaje.

## REST API

- `GET /api/categories/` — lista kategorii (`main_category`, `spec_schema`) — publiczny
- `GET /api/products/` — lista produktów; filtry:
  `?category=`, `?brand=`, `?category__main_category=` — publiczny
- `GET /api/products/{id}/` — szczegóły produktu (spec, pros/cons summary) — publiczny
- `POST /api/products/{id}/refresh-reviews/` — ręcznie triggeruje pobranie
  opinii z aktywnych `ReviewSource` **pasujących do kategorii produktu**
  (kolejkuje zadania Celery przez `scrapers.tasks.queue_fetch`). **Tylko dla
  staff/adminów** (`IsAdminUser` — narzędzie operatorskie, nie coś, z czego
  korzysta wyszukiwarka publiczna), throttling `10/hour` (scope
  `refresh-reviews`).
- `POST /api/products/{id}/fetch-crawlbase-reddit-post/` —
  `{"url": "https://www.reddit.com/..."}`: pobiera *ten jeden konkretny*
  post z Reddita przez Crawlbase, niezależnie od `refresh-reviews`/kategorii
  — wymaga wcześniej utworzonego `ReviewSource` z
  `parser_key="crawlbase_reddit"`. **Tylko dla staff/adminów**, throttling
  `20/hour` (scope `crawlbase-fetch`), i waliduje że `url` faktycznie
  wskazuje na `reddit.com` (`ProductViewSet._is_reddit_url` w
  `products/views.py`) — inaczej `400`, żeby endpoint nie stał się otwartym
  proxy do scrapowania dowolnego URL-a przez Crawlbase na koszt projektu.
- `GET /api/reviews/?product={id}&sentiment=positive&source={id}` — lista
  opinii z filtrowaniem po produkcie/sentymencie/źródle — publiczny

Limity throttlingu konfigurowalne w `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`
(`settings/base.py`). Te dwa endpointy (`refresh-reviews`,
`fetch-crawlbase-reddit-post`) wymagają `IsAdminUser` — **staff/superuser**
(`python manage.py createsuperuser` albo `is_staff=True` w adminie), nie
zwykłego konta zarejestrowanego przez `/accounts/register/` (patrz "Konta
użytkowników" wyżej — to osobny, nieuprzywilejowany poziom, celowo bez
dostępu do tych narzędzi operatorskich). DRF domyślnie akceptuje sesję admina
(zalogowanego w `/admin/`) lub HTTP Basic Auth dla tych dwóch endpointów.

## Monitoring kosztów API zewnętrznych

Każde realne wywołanie zewnętrznego API (Anthropic, YouTube, Google Places,
Crawlbase, Reddit) zapisuje jeden wiersz `APIUsageLog`
(`analysis/models.py`) — przez wspólny helper `analysis/usage.py`
(`log_api_usage`/`track_api_usage`), nie zduplikowaną logikę per plik.
`/admin/analysis/apiusagelog/` pokazuje sumy zużycia (tokeny dla Anthropic,
liczba requestów dla reszty) z ostatnich 24h i 7 dni per provider, nad
zwykłą listą wpisów. Przekroczenie dziennego budżetu (`DAILY_*_BUDGET` w
`.env`, domyślnie wyłączone — `0`) loguje `CRITICAL` (`analysis/usage.py`,
z jawnym `# TODO` na integrację ze Slackiem/Sentry — na razie tylko log).

## Dodawanie nowego źródła opinii

`ReviewSource.main_categories` określa, dla jakich głównych kategorii
(`Category.MainCategory`) źródło jest właściwe — pusta lista oznacza "dla
wszystkich". Dzięki temu np. forum kosmetyczne nie zostanie odpytane dla
elektroniki. `ReviewSource.source_type` (`reddit`/`forum`/`review_site`/
`other`) decyduje, który Celery task obsłuży dane źródło
(`scrapers.tasks.queue_fetch`): `reddit` ma dedykowany task, wszystko inne
(`forum`, `review_site`, `other`) trafia do wspólnego
`fetch_registry_reviews`, bo każde z nich rozwiązuje swój parser przez ten
sam rejestr (`scrapers/registry.py::PARSER_REGISTRY`) — niezależnie od tego,
czy parser scrapuje HTML, czy woła oficjalne REST API.

**Reddit dla nowej kategorii** — bez zmian w kodzie: utwórz kolejny rekord
`ReviewSource` z `source_type=reddit`, ustaw `main_categories` (np.
`["cosmetics"]`) i `parser_config={"subreddits": ["SkincareAddiction", ...]}`.
Bez skonfigurowanych subredditów źródło używa globalnego fallbacku
`REDDIT_SUBREDDITS` z `.env`.

**Nowe forum na znanym silniku** (np. kolejne phpBB) — też bez nowego pliku
Python: utwórz `ReviewSource` z `parser_key="generic_forum"`,
`source_type=forum`, `main_categories` odpowiednie dla tematyki forum, i
`parser_config` z selektorami CSS (`thread_link_selector`, `post_selector`,
`body_selector`, `author_selector`, `date_selector` — patrz
`scrapers/generic_forum_scraper.py` po pełną listę i wartości domyślne).

**Nowe forum o unikalnym układzie strony** (bespoke parser) — gdy selektory
per-`parser_config` nie wystarczają:
1. Napisz `scrapers/<nazwa>_scraper.py` z funkcją
   `fetch(product_name, base_url, parser_config=None)` zwracającą
   `FetchedReview` (patrz `scrapers/forum_scraper_example.py` — elektronika,
   lub `scrapers/beauty_forum_scraper.py` — kosmetyki, jako drugi przykład
   potwierdzający że architektura skaluje się na kolejne kategorie
   tematyczne).
2. Dodaj wpis w `scrapers/registry.py` (`PARSER_REGISTRY`).
3. Utwórz rekord `ReviewSource` z `parser_key` odpowiadającym kluczowi z
   rejestru i właściwym `main_categories`.

**Oficjalne API zamiast web scrapingu** (np. Google Places, YouTube Data
API) — trzeci wzorzec, ten sam kontrakt co powyżej: napisz
`scrapers/<nazwa>_scraper.py` z `fetch(product_name, base_url,
parser_config=None)` wołającą oficjalne REST API przez `requests` (patrz
`scrapers/google_places_scraper.py` — Google Places Text Search + Place
Details, `parser_config={"place_id": ...}` żeby pominąć wyszukiwanie, oraz
`scrapers/youtube_scraper.py` — `search.list` + `commentThreads.list`,
`parser_config={"max_videos": ..., "max_comments_per_video": ...}`), dodaj
do `PARSER_REGISTRY`, utwórz `ReviewSource` z `source_type=review_site` i
kluczem API w `.env` (`GOOGLE_PLACES_API_KEY`/`YOUTUBE_API_KEY`). Trafiają do
tego samego `fetch_registry_reviews`, co fora — brak scrapingu HTML nie
zmienia integracji z resztą systemu.

**Pobranie jednego konkretnego, znanego URL-a** (Crawlbase) — inny kształt
niż powyższe: zamiast szukać opinii po nazwie produktu w ramach źródła,
Crawlbase (`scraper=reddit-post`) dostaje z góry konkretny URL posta z
Reddita (np. z endpointu `POST /api/products/{id}/fetch-crawlbase-reddit-post/`).
`scrapers/crawlbase_reddit_scraper.py::fetch_reddit_post(url)` woła
Crawlbase (klucz w `.env`: `CRAWLBASE_TOKEN`) i zwraca surowe dane posta;
dedykowany task `scrapers/tasks.py::fetch_crawlbase_reddit_post` (nie
`queue_fetch`/`fetch_registry_reviews` — ten przepływ jest wywoływany
jawnie z URL-em, nie rozsyłany automatycznie po kategorii) sam robi upsert
do `Review` po `source_url_hash`, **pomijając ponowne pobranie, jeśli mamy
już świeży (< 24h) wpis** — inaczej niż stały, nigdy-nie-odświeżający dedup
w `scrapers/dedup.py`. Wymaga `ReviewSource` z `parser_key="crawlbase_reddit"`
(utworzony ręcznie w adminie, jak każde inne źródło).

Deduplikacja po hashu URL-a (`scrapers/dedup.py`) działa identycznie dla
każdego typu źródła, który przez nią przechodzi (wszystkie powyższe poza
Crawlbase) — nowy parser nie wymaga żadnych zmian w tej warstwie.

**Ważne (decyzja biznesowa, nie techniczna) — przy KAŻDYM nowym scraperze
opartym o web scraping (fora):** przed włączeniem go na konkretne forum
sprawdź jego `robots.txt` i regulamin (ToS). Ten projekt nie automatyzuje tej
weryfikacji — patrz komentarz w `scrapers/forum_scraper_example.py` /
`generic_forum_scraper.py` / `beauty_forum_scraper.py`. Źródła oparte o
oficjalne/zewnętrzne API (Google Places, YouTube, Crawlbase) **nie
wymagają** tej weryfikacji — to nie scraping HTML — ale mają własne
limity/koszty, które trzeba sprawdzić przed włączeniem na produkcji: Google
Places ma limity zapytań i billing, YouTube Data API ma dzienny quota,
Crawlbase rozlicza się per żądanie (stąd też cache 24h w
`fetch_crawlbase_reddit_post`, żeby nie płacić za ten sam URL wielokrotnie).
Niezależnie od typu źródła zapisz wynik
weryfikacji w samym rekordzie `ReviewSource` (`tos_checked_at`, `tos_notes`
— dla API-based źródeł użyj `tos_notes` na limity/quota, dla spójności śladu
weryfikacji w bazie) — to jedyny wymagany krok po
stronie danych; sama weryfikacja pozostaje ręczna i musi być wykonana zanim
`is_active` zostanie ustawione na `True`. Nie scrapuj serwisów, które wprost
zabraniają tego w ToS (np. Amazon).

## Analiza sentymentu i podsumowań

`analysis/prompts.py` wstrzykuje kontekst kategorii produktu do promptów
Claude: etykietę kategorii (`CATEGORY_LABELS`) dla klasyfikacji sentymentu
(`sentiment.py`) oraz dodatkowo aspekty, na które warto zwrócić uwagę
(`CATEGORY_ASPECT_HINTS`) dla zbiorczego podsumowania zalet/wad
(`summarizer.py`) — np. dla kosmetyków: reakcja skóry/włosów, zapach,
alergie; dla elektroniki: trwałość, wydajność, jakość wykonania. Format
wyniku (`{"sentiment": ...}` / `{"pros": ..., "cons": ...}`) jest identyczny
niezależnie od kategorii — zmienia się tylko treść promptu, nie kontrakt
danych. Nieznana/pusta kategoria używa domyślnie kontekstu elektroniki.

## Testy

```bash
pytest
```

Obejmują m.in.:
- walidację `Product.specification` względem `Category.spec_schema` per
  kategoria, w tym przypadki niepoprawne (`products/tests.py`)
- dopasowanie `ReviewSource` → kategoria produktu (`ReviewSource.applies_to`)
  i dispatch Celery po `source_type` (`reviews/tests.py`)
- deduplikację opinii po hashu URL-a (`reviews/tests.py`)
- zmockowaną integrację z Reddit API i z generycznym parserem forum na
  zmockowanym HTML, bez realnych zapytań sieciowych (`scrapers/tests.py`)
- kontekst kategorii wstrzykiwany do promptów Claude, retry/backoff i
  rozróżnienie błędów trwałych/przejściowych, zmockowanego klienta Anthropic
  (`analysis/tests.py`)
- autoryzację/throttling/walidację URL na `refresh-reviews`/
  `fetch-crawlbase-reddit-post` oraz bramkę kosztową (honeypot, limity
  per-IP/dzienny, brak tworzenia `Product` po przekroczeniu limitu) na
  `product_search` (`products/tests.py`)
- logowanie zużycia API (`APIUsageLog`) i próg alertowy per provider
  (`analysis/tests.py`)
- rejestrację/logowanie/wylogowanie i listę obserwowanych produktów
  (`accounts/tests.py`)
- własne oceny użytkowników — upsert, walidację zakresu 1-5
  (`reviews/tests.py`)
- zgłaszanie nietrafnych podsumowań AI — idempotencję, walidację `target`
  (`analysis/tests.py`)
- pełny cykl życia konta — reset hasła (`django.core.mail.outbox`, pełny
  przepływ e-mail→link→nowe hasło), zmiana hasła (sesja przeżywa zmianę),
  zmiana e-maila (unikalność), throttling `register()`, kontekst strony
  profilu (`accounts/tests.py`)

Autouse fixture w `conftest.py` wymusza `LocMemCache` na czas testów, więc
cały pakiet działa bez żywego Redisa (produkcyjnie cache i broker Celery to
osobne instancje/DB-index Redisa, patrz `.env.example`).

## Rozwiązywanie problemów

**`SSLCertVerificationError: unable to get local issuer certificate` przy
wywołaniach do zewnętrznych API (Google/YouTube/Anthropic/Crawlbase)** — to
problem lokalnego środowiska Windows (typowo: antywirus/firewall robiący
inspekcję SSL, którego certyfikat nie jest w zaufanym magazynie Pythona), nie
błąd w kodzie. Najprostsza poprawka: `pip install pip-system-certs` w venv —
przełącza Pythona na system Windows do weryfikacji certyfikatów. Nie jest w
`requirements.txt`, bo to fix dla konkretnej maszyny/sieci, nie zależność
projektu.

## Ograniczenia na tym etapie

- Frontend to tylko prosta wyszukiwarka server-side (bez JS/SPA) — do
  pełnej integracji z zewnętrznym frontendem służy REST API (DRF).
- Brak priorytetowych kolejek — zwykły Celery + Celery Beat wystarcza.
- Parsery forów (`forum_scraper_example.py`, `beauty_forum_scraper.py`,
  `generic_forum_scraper.py`) są szkieletami z ilustracyjnymi selektorami —
  wymagają dostosowania do realnego, zgodnego z ToS źródła przed użyciem
  produkcyjnym.
- Dodanie zupełnie nowej głównej kategorii (poza elektroniką/kosmetykami/
  domem) to wciąż zmiana w kodzie (`Category.MainCategory`,
  `products/spec_schemas.py`, `analysis/prompts.py`), nie tylko w danych.
- Klucze API wyłącznie przez zmienne środowiskowe (`.env`, nigdy
  hardcodowane / commitowane).
- Konta użytkowników (`accounts`) mają reset/zmianę hasła, zmianę e-maila i
  prosty profil, ale brak weryfikacji adresu e-mail (nowy e-mail
  zapisywany od razu, bez potwierdzającego linku) i profilu poza tym, co
  daje `django.contrib.auth.User`.
- `SummaryFlag` (zgłoszenia nietrafnych podsumowań AI) nie ma workflow
  moderacji — zgłoszenia są widoczne tylko w panelu admina, nikt nie jest o
  nich automatycznie powiadamiany.
