# ReviewRadar — dokumentacja funkcji

Szczegółowy opis każdej funkcji/metody w projekcie, pogrupowany według aplikacji Django (`products`, `reviews`, `scrapers`, `analysis`). Dla ogólnego opisu architektury, instalacji i uruchomienia patrz [README.md](README.md); dla kontekstu przeznaczonego dla Claude Code patrz [CLAUDE.md](CLAUDE.md).

Konwencja opisu: **sygnatura**, do czego służy, parametry, co zwraca, efekty uboczne i obsługa błędów.

---

## Spis treści

- [accounts](#accounts)
- [products](#products)
- [reviews](#reviews)
- [scrapers](#scrapers)
- [analysis](#analysis)

---

## accounts

### `accounts/models.py`

#### `Watchlist`
Łącznik user↔product oznaczający "obserwowany produkt". `user`/`product` (oba `CASCADE`), `unique_together=("user","product")` — jeden wpis na parę, więc "obserwuj"/"przestań obserwować" to zawsze albo stworzenie, albo usunięcie dokładnie jednego wiersza (patrz `products.web_views.toggle_watchlist`), nigdy duplikat.

### `accounts/forms.py`

#### `RegistrationForm(UserCreationForm)`
Standardowy `UserCreationForm` Django (walidacja siły hasła, potwierdzenie hasła, unikalność `username`) rozszerzony o wymagane, unikalne pole `email` (`clean_email` odrzuca, jeśli `User` z tym adresem już istnieje) — unikalność jest wymagana od czasu wprowadzenia resetu hasła po e-mailu (patrz `views.py` niżej), żeby jeden adres jednoznacznie wskazywał jedno konto.

#### `EmailChangeForm(forms.Form)`
Formularz zmiany adresu e-mail zalogowanego użytkownika. Przyjmuje `user` w `__init__` (wzorem Django `PasswordChangeForm`) i w `clean_email` odrzuca adres już używany przez **inne** konto (`User.objects.exclude(pk=self.user.pk).filter(email=...)` — wyklucza siebie, więc zapisanie tego samego adresu jest no-opem, nie błędem).

### `accounts/views.py`

#### `_register_allowed(request) -> bool`
Prywatny per-IP, fixed-window licznik rejestracji (`f"register:ip:{ip}:{godzina}"`, limit `settings.REGISTER_IP_RATE_PER_HOUR`, domyślnie 5/h) — woła wprost `products.rate_limit.increment_and_check`, ten sam prymityw co bramka kosztowa wyszukiwarki, zamiast duplikować logikę licznika w nowej aplikacji.

#### `register(request)`
`GET` renderuje pusty `RegistrationForm`. `POST` najpierw sprawdza `_register_allowed(request)` — po przekroczeniu limitu: komunikat błędu + redirect do `accounts:register`, formularz nawet nie jest tworzony (ochrona przed floodem kont botów, nie tylko przed nadużyciem konkretnego zgłoszenia). W przeciwnym razie: poprawny `POST` tworzy `User`, od razu loguje go (`django.contrib.auth.login`) i przekierowuje do wyszukiwarki z komunikatem powitalnym — użytkownik nie musi się osobno logować po rejestracji.

#### `watchlist_view(request)` *(`@login_required`)*
Renderuje `/accounts/watchlist/`: `request.user.watchlist.select_related("product__category")`, spłaszczone do listy `Product` (`[entry.product for entry in entries]`) — dzięki temu szablon może reużyć ten sam partial `templates/products/_product_grid.html`, co strona wyszukiwania, zamiast duplikować markup karty produktu.

#### `profile_view(request)` *(`@login_required`)*
Renderuje `/accounts/profile/`: `watchlist_count`/`review_count` (`request.user.watchlist.count()`/`request.user.product_reviews.count()`) obok danych z `django.contrib.auth.User` (`username`/`email`/`date_joined`, czytane w szablonie wprost z `user`, bez osobnego kontekstu).

#### `change_email(request)` *(`@login_required`)*
Jedyny widok w tym pliku bez odpowiednika w Django. `GET` pokazuje `EmailChangeForm` wypełniony obecnym adresem; poprawny `POST` zapisuje `request.user.email` (`save(update_fields=["email"])` — tylko to jedno pole) i przekierowuje do profilu z komunikatem. Bez ponownego podania hasła — aktywna sesja już potwierdza tożsamość (w przeciwieństwie do zmiany hasła, gdzie stare hasło i tak pilnuje wbudowany `PasswordChangeView`).

Logowanie/wylogowanie/zmiana hasła/reset hasła **nie** mają własnych widoków — `accounts/urls.py` używa wprost `django.contrib.auth.views.LoginView`/`LogoutView`/`PasswordChangeView`/`PasswordChangeDoneView`/`PasswordResetView`/`PasswordResetDoneView`/`PasswordResetConfirmView`/`PasswordResetCompleteView`, każdy tylko z `template_name` (i `success_url`/`email_template_name`/`subject_template_name` gdzie potrzebne, przez `reverse_lazy`). Uwaga: `LogoutView` w Django 5.2 akceptuje tylko `POST` (`http_method_names = ["post", "options"]`) — link "Wyloguj" w nawigacji musi być formularzem z przyciskiem, nie `<a href>`. Reset hasła nie wymaga `django.contrib.sites` — `PasswordResetView` przekazuje `request` do `form.save()`, więc `get_current_site()` samo spada na `RequestSite` (domena wzięta z requesta) zamiast szukać wpisu w tabeli `Site`.

### `accounts/admin.py`

#### `WatchlistAdmin` (`ModelAdmin`)
Standardowa konfiguracja bez własnej logiki: `list_display=("user","product","created_at")`, `list_filter=("created_at",)`.

---

## products

### `products/models.py`

#### `Category.save(self, *args, **kwargs)`
Nadpisany `save()` modelu `Category`. Przed zapisem: (1) jeśli `slug` jest puste, generuje je z `name` przez `slugify()`; (2) jeśli `spec_schema` jest puste (`{}`/`None`/falsy), pobiera domyślny szablon JSON Schema dla `main_category` z `products/spec_schemas.py::SPEC_SCHEMAS` (import wewnątrz metody, żeby uniknąć cyklicznego importu `models.py` ↔ `spec_schemas.py`). Dzięki temu nowa kategoria automatycznie dziedziczy schemat swojej głównej kategorii, chyba że admin poda własny.

#### `validate_specification(category, specification: dict) -> None`
*(`products/validation.py`)* Waliduje `Product.specification` względem `category.spec_schema` (JSON Schema, draft-07) przez `jsonschema.validate()`. Jeśli `category` jest `None` lub `spec_schema` jest puste — walidacja jest no-opem (brak ograniczeń, np. kategoria bez przypisanego schematu). Przy błędzie walidacji podnosi Django `ValidationError` z czytelnym komunikatem po polsku wskazującym ścieżkę pola (`exc.absolute_path`) i przyczynę.

#### `Product.clean(self)`
Nadpisana walidacja modelu. Wywołuje `super().clean()`, a następnie `validate_specification(self.category, self.specification)`. Uruchamia się automatycznie przy `ModelForm.full_clean()` (czyli w panelu admina) — **nie** przy zwykłym `.save()`/`.objects.create()`, więc dane tworzone programistycznie (np. przez scrapery) nie są tym blokowane.

---

### `products/web_views.py` (server-rendered UI, bez JS)

#### `_resolve_category_for_new_product(category_slug: str, main_category: str)`
Prywatna funkcja pomocnicza `product_search()`. Wyznacza `Category`, do której ma zostać przypisany automatycznie tworzony "stub" produkt, gdy wyszukiwana fraza nie pasuje do niczego w katalogu:
- jeśli podano `category_slug` — zwraca dokładnie tę kategorię (`Category.objects.filter(slug=...).first()`, może zwrócić `None` jeśli slug nie istnieje);
- w przeciwnym razie, jeśli podano `main_category` — get-or-create kategorii-worka o nazwie `"Inne (<etykieta głównej kategorii>)"` (np. "Inne (Elektronika)"), przypisanej do tej `main_category`; nieprawidłowa wartość `main_category` (np. spreparowany URL) łapana przez `try/except ValueError` przy `Category.MainCategory(main_category)` i zwraca `None`;
- jeśli nie podano żadnego filtra — zwraca `None` (świadoma decyzja: bez kontekstu kategorii nie da się sensownie zgadnąć, do jakiej kategorii należy wpisana fraza).

#### `_get_or_create_stub_product(query: str, category: Category) -> Product`
Tworzy minimalny rekord `Product` dla wyszukiwanej frazy, która nie pasowała do niczego w bazie — żeby `queue_live_search_fetch()` miało do czego podpiąć pobrane opinie. `brand`/`model_name` to zgrubny podział frazy po pierwszej spacji (`query.split(None, 1)`; pierwszy człon = `brand`, reszta = `model_name`, oba przycięte do 100 znaków — limit pola). Jeśli taki `brand`+`model_name` już istnieje (unique_together, `IntegrityError`) — np. ta sama fraza była wcześniej wyszukana pod innym filtrem kategorii — funkcja **nie** tworzy duplikatu, tylko zwraca istniejący rekord (bez zmiany jego kategorii).

#### `_is_meaningful_query(query: str) -> bool`
Prywatna heurystyka `product_search()` — gatuje **wyłącznie** auto-tworzenie stub-produktu (nie wpływa na zwykłe wyszukiwanie w bazie ani na live-fetch dla już istniejących dopasowań). Odrzuca frazy krótsze niż `MIN_MEANINGFUL_QUERY_LENGTH` (=3) po `strip()`, oraz frazy złożone z jednego powtórzonego znaku (`len(set(stripped.lower())) > 1`, np. `"aaaa"`). Świadomie prosta — nie próbuje wykrywać spamu/losowości ogólnie, tylko odsiewa najtańsze przypadki nadużycia formularza.

#### `product_search(request)`
Główny widok wyszukiwarki (`GET /`), **publiczny i bez limitów dla samego przeszukiwania bazy** — ograniczenia dotyczą wyłącznie ścieżek generujących koszt (auto-tworzenie produktu + `queue_live_search_fetch`). Przebieg:
1. Czyta `q`, `category` (slug), `main_category` z query stringa, oraz honeypot `website` (ukryte pole formularza w `templates/base.html` — wypełnione tylko przez boty).
2. Filtruje `Product.objects.select_related("category")` przez `icontains` na `name`/`brand`/`model_name`/`category__name` (jeśli `q`), oraz dokładnie po `category__slug`/`category__main_category` (jeśli podane) — ten krok zawsze się wykonuje, niezależnie od poniższej bramki kosztowej.
3. **Bramka kosztowa:** `cost_path_ok = bool(query) and not is_honeypot_triggered and cost_path_allowed(request)` (patrz `products/rate_limit.py::cost_path_allowed`), obliczane **dokładnie raz** na request (short-circuit — `cost_path_allowed` ma efekty uboczne, inkrementuje liczniki, więc nie może być wołane więcej niż raz ani przy pustym `q`).
4. **Auto-tworzenie produktu:** jeśli `q` niepuste, wynik pusty, podano `category` lub `main_category`, `_is_meaningful_query(query)` jest `True`, **i** `cost_path_ok` — woła `_resolve_category_for_new_product()` i `_get_or_create_stub_product()`, po czym `products` staje się jednoelementowym querysetem z nowo utworzonym produktem. Jeśli tylko `cost_path_ok` blokuje (zapytanie było sensowne) — ustawia `cost_path_limited = True` do komunikatu w szablonie; blokada przez `_is_meaningful_query` jest cicha (brak komunikatu, traktowana jak zwykły brak wyników).
5. **Live fetch:** jeśli `cost_path_ok`, dla pierwszych `MAX_LIVE_FETCH_PRODUCTS` (=5) wyników (istniejących **i** nowo utworzonego stuba — jeden wspólny gate, patrz decyzja projektowa w `CLAUDE.md`) woła `queue_live_search_fetch(product)` (kolejkuje pobranie opinii z YouTube/Google Places w tle, patrz `scrapers/tasks.py`).
6. Renderuje `products/search.html` z kontekstem: `query`, `selected_category`, `selected_main_category`, `main_categories` (wybór do selecta), `categories` (pogrupowane po `main_category` do `{% regroup %}`), `products`, `result_count`, `auto_created_product` (banner "dodano nowy wpis"), `cost_path_limited` (banner "osiągnięto limit").

#### `product_detail(request, pk)`
Widok szczegółów produktu (`GET /products/<pk>/`). Przebieg:
1. Pobiera `Product` (404 jeśli nie istnieje) z `select_related("category")`.
2. `reviews` — do 50 powiązanych `Review` (`select_related("source")`); `youtube_reviews`/`google_places_reviews` — podzbiory filtrowane po `source.parser_key` (`"youtube"`/`"google_places"`), każdy z osobną zakładką na stronie.
3. `fetching_sources` — nazwy źródeł z aktywnym `ScrapeJob` (`PENDING`/`RUNNING`) dla tego produktu — steruje bannerem "pobieranie w toku".
4. **Auto-generowanie AI overview:** jeśli `product.ai_overview` puste i jeszcze nie zażądano (`ai_overview_requested_at` puste) — woła `queue_ai_overview(product)` (patrz `analysis/tasks.py`), które kolejkuje zapytanie do Claude. Ustawia `ai_overview_pending = True`, dopóki `ai_overview` jest puste — dzięki temu generacja odpala się automatycznie przy pierwszym wejściu, ale **tylko raz** (kolejne odświeżenia strony nie duplikują zapytania).
5. `needs_refresh` — `True` gdy trwa dowolne pobieranie (`fetching_sources` niepuste) lub generowanie AI overview — steruje znacznikiem `<meta http-equiv="refresh" content="5">` w `templates/products/detail.html` (auto-odświeżenie strony co 5 s, żeby użytkownik zobaczył wynik bez ręcznego reloadu).
6. **Konta użytkowników** — `is_watched` (`accounts.models.Watchlist` istnieje dla `request.user`+`product`, zawsze `False` dla anonimowych); `user_reviews` (do 50 `reviews.models.UserReview`, `select_related("user")`); `avg_rating`/`review_count` (`product.user_reviews.aggregate(Avg("rating"), Count("id"))`); `my_review` (własna ocena zalogowanego użytkownika albo `None`) i `review_form` (`UserReviewForm(instance=my_review)`, prefill do edycji).

#### `toggle_watchlist(request, pk)` *(`@login_required`, `@require_POST`)*
`Watchlist.objects.get_or_create(user=request.user, product=product)` — jeśli wiersz już istniał (`created=False`), usuwa go zamiast zostawić duplikat: jeden endpoint obsługuje dodanie i usunięcie, klient nie musi znać aktualnego stanu. Redirect z powrotem do `products:detail` z komunikatem (`django.contrib.messages`).

#### `submit_user_review(request, pk)` *(`@login_required`, `@require_POST`)*
Upsert własnej oceny: pobiera istniejący `UserReview` (jeśli jest) jako `instance` dla `UserReviewForm(request.POST, instance=existing)` — zapis nadpisuje poprzednią ocenę tego samego użytkownika zamiast tworzyć drugi wiersz (`unique_together` i tak by to wymusiło, ale bez `instance` skończyłoby się `IntegrityError` zamiast update'u). Nieprawidłowy formularz (np. `rating` poza 1-5) → komunikat błędu, brak zapisu. Zawsze redirect do `products:detail` (wzorzec Post/Redirect/Get — błędy walidacji nie są przenoszone przez GET, tylko zgłaszane komunikatem).

#### `flag_summary(request, pk)` *(`@login_required`, `@require_POST`)*
`target` z body musi być `"pros"` lub `"cons"` (`SummaryFlag.Target.values`) — inaczej `400`. `SummaryFlag.objects.get_or_create(...)` — drugie zgłoszenie tego samego użytkownika+produktu+targetu jest no-opem (`created=False`), zwraca inny komunikat ("już zgłoszono") zamiast tworzyć duplikat.

---

### `products/views.py` (REST API, DRF)

#### `CategoryViewSet` (`ReadOnlyModelViewSet`)
`GET /api/categories/`, `GET /api/categories/{id}/` — lista/szczegóły kategorii przez `CategorySerializer`. Bez własnej logiki poza deklaracją `queryset`/`serializer_class`.

#### `_is_reddit_url(url: str) -> bool`
Waliduje, że podany URL faktycznie wskazuje na reddit.com, zanim trafi do Crawlbase (który pobierze dowolny URL — bez tej kontroli endpoint byłby otwartym proxy do scrapowania czegokolwiek). `urlparse(url)`, wymaga `scheme in {"http", "https"}` **i** (`netloc == "reddit.com"` lub `netloc.endswith(".reddit.com")`) — pokrywa `www.reddit.com`/`old.reddit.com`, odrzuca `reddit.com.evil.com` (inny `netloc`, `endswith` nie łapie tego bez kropki na początku) i `notreddit.com`.

#### `ProductViewSet.get_serializer_class(self)`
Wybiera serializer w zależności od akcji: `ProductListSerializer` dla `list`, `ProductDetailSerializer` dla pozostałych akcji (`retrieve` itd.) — lista jest lżejsza (kategoria jako sam slug), szczegóły zawierają pełną specyfikację i podsumowania.

#### `ProductViewSet.throttle_scope = None` (atrybut klasowy)
Wymagany, żeby `@action(..., throttle_scope="...")` w ogóle zadziałało: `APIView` (rodzic `ViewSet`) nie deklaruje `throttle_scope` jako atrybutu klasy, a `ViewSet.as_view()` sprawdza `hasattr(cls, key)` dla każdego kwargu z `@action` i rzuca `TypeError` przy starcie serwera, jeśli atrybut nie istnieje na klasie. `permission_classes`/`throttle_classes` nie potrzebują tego zabiegu, bo `APIView` już je definiuje.

#### `ProductViewSet.refresh_reviews(self, request, pk=None)`
`POST /api/products/{id}/refresh-reviews/`. **Wymaga `IsAdminUser`** (projekt nie ma kont "zwykłych" użytkowników — tylko Django staff/superuser) i jest throttlowane `ScopedRateThrottle` (`throttle_scope="refresh-reviews"`, domyślnie `10/hour`, `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`) — te ustawienia obowiązują tylko dla tej akcji, nie całego `ProductViewSet` (przekazane przez `@action(...)` kwargs, zastosowane jako atrybuty instancji tylko dla tej trasy). Ręcznie (re)triggeruje pobranie opinii dla produktu ze **wszystkich** aktywnych `ReviewSource`, których `applies_to(product.category.main_category)` zwraca `True`. Jeśli brak pasujących źródeł — `400` z komunikatem. W przeciwnym razie woła `queue_fetch(source, product)` dla każdego pasującego źródła i zwraca `202` z listą nazw zakolejkowanych źródeł (`queued_sources`).

#### `ProductViewSet.fetch_crawlbase_reddit_post(self, request, pk=None)`
`POST /api/products/{id}/fetch-crawlbase-reddit-post/`, body `{"url": "..."}`. Ta sama ochrona co `refresh_reviews` (`IsAdminUser` + `ScopedRateThrottle`, `throttle_scope="crawlbase-fetch"`, domyślnie `20/hour`), plus walidacja przez `_is_reddit_url(url)` **przed** kolejkowaniem — URL spoza domeny reddit.com → `400`, bez zużycia limitu Crawlbase. Pobiera **jeden konkretny**, podany URL posta z Reddita przez Crawlbase — niezależnie od kategorii produktu. Dalsza walidacja: brak `url` w body → `400`; brak aktywnego `ReviewSource` z `parser_key="crawlbase_reddit"` → `400` (źródło musi być utworzone ręcznie w adminie, tak jak każde inne — brak auto-`get_or_create`). W przeciwnym razie kolejkuje `fetch_crawlbase_reddit_post_task.delay(product.id, source.id, url)` (import z `scrapers.tasks` pod aliasem, żeby nie kolidować nazwą z tą metodą) i zwraca `202`.

---

### `products/rate_limit.py` (generyczny fixed-window rate limiter, nie tylko dla `product_search`)

#### `increment_and_check(cache_key: str, limit: int, window_seconds: int) -> bool`
Fixed-window licznik na cache Django: `cache.incr(cache_key)`, a jeśli klucz jeszcze nie istnieje (`ValueError`) — `cache.set(cache_key, 1, timeout=window_seconds)`. Zwraca `True`, jeśli licznik po inkrementacji wciąż mieści się w `limit` (licznik **zawsze** jest inkrementowany, niezależnie od wyniku — to celowe: żądanie, które przekroczyło limit, nadal się "liczy" do okna). Prostsze niż sliding window, wystarczające do ochrony przed nadużyciem, nie do precyzyjnego SLA. Publiczna funkcja (bez wiodącego `_`) — mieszka w `products`, bo ta aplikacja potrzebowała jej pierwsza (`cost_path_allowed` niżej), ale to generyczny prymityw reużywany też przez `accounts.views._register_allowed`; nowe limity per-IP/globalne powinny reużywać tej funkcji, nie duplikować liczników.

#### `cost_path_allowed(request) -> bool`
Woła `increment_and_check` dwukrotnie: raz per-IP w oknie godzinowym (klucz z `REMOTE_ADDR` + zaokrągloną do godziny datą, limit `settings.SEARCH_COST_PATH_IP_RATE_PER_HOUR`), raz globalnie w oknie dobowym (klucz z datą, limit `settings.DAILY_NEW_PRODUCT_LIMIT`) — zwraca `True` tylko jeśli oba liczniki wciąż pod limitem. Wywoływane wyłącznie z `product_search`, dokładnie raz na request z niepustym `q` (ma efekty uboczne — patrz `product_search` wyżej).

---

### `products/admin.py`

#### `CategoryAdmin` (`ModelAdmin`)
Standardowy admin dla `Category`: `list_display`/`list_filter` po `main_category`, `prepopulated_fields` auto-uzupełniające `slug` z `name`.

#### `ProductAdmin.changeform_view(self, request, object_id=None, form_url="", extra_context=None)`
Nadpisany widok formularza dodawania/edycji `Product` w adminie. Buduje mapę `{category.id: category.spec_schema}` dla wszystkich kategorii i wstrzykuje ją jako JSON (`category_schema_map_json`) do kontekstu szablonu, który następnie (przez `templates/admin/products/product/change_form.html` + `products/static/products/admin_spec_schema.js`) renderuje czytelną podpowiedź oczekiwanych pól specyfikacji, aktualizowaną na żywo przy zmianie wybranej kategorii (czysty JS po stronie klienta, bez zapytań AJAX — cała mapa jest osadzona w stronie od razu).

---

### `products/serializers.py`
Trzy serializery DRF bez własnej logiki poza deklaracją pól: `CategorySerializer` (`id, name, slug, main_category, spec_schema`), `ProductListSerializer` (kategoria jako slug przez `SlugRelatedField`), `ProductDetailSerializer` (kategoria zagnieżdżona pełnym `CategorySerializer`, plus `specification`/`pros_summary`/`cons_summary`/`summary_updated_at`).

---

## reviews

### `reviews/models.py`

#### `ReviewSource.applies_to(self, main_category: str) -> bool`
Sprawdza, czy dane źródło opinii jest właściwe dla podanej głównej kategorii produktu: zwraca `True`, jeśli `main_categories` jest puste (źródło uniwersalne, np. Google Places/YouTube domyślnie) **lub** `main_category` znajduje się na liście. Używane wszędzie tam, gdzie trzeba przefiltrować źródła pod kątem produktu: `queue_live_search_fetch()`, `refresh_recent_products()`, `ProductViewSet.refresh_reviews`.

#### `Review.save(self, *args, **kwargs)`
Nadpisany `save()`: jeśli `source_url_hash` jest puste, oblicza je z `source_url` przez `hash_url()` przed zapisem. Dzięki temu wywołujący (np. `dedup.save_reviews`) nie musi ręcznie liczyć hasha przy tworzeniu (choć `save_reviews`/`fetch_crawlbase_reddit_post` i tak liczą go jawnie, bo potrzebują go *przed* zapisem do sprawdzenia duplikatu).

#### `Review.hash_url(url: str) -> str` *(staticmethod)*
Liczy SHA-256 z przyciętego (`.strip()`) URL-a źródłowego, zakodowanego jako UTF-8. To jedyny mechanizm deduplikacji opinii w całym projekcie — identyczny URL zawsze daje identyczny hash, niezależnie od źródła (Reddit, forum, Google Places, YouTube, Crawlbase).

#### `UserReview`
Ocena 1-5 + opcjonalny komentarz wystawiona bezpośrednio przez zalogowanego użytkownika — model odrębny od `Review` (bez `ReviewSource`, bez sentymentu, bez deduplikacji po URL-u). `rating` ma `MinValueValidator(1)`/`MaxValueValidator(5)` jako drugą linię obrony (pierwsza to `reviews/forms.py::UserReviewForm` na granicy żądania). `unique_together=("product","user")` — jedna, edytowalna ocena na użytkownika; `products.web_views.submit_user_review` robi upsert (patrz tam), nie insert-only.

### `reviews/forms.py`

#### `UserReviewForm(forms.ModelForm)`
Waliduje `rating`/`comment` z POST-a przed zapisem `UserReview` — `rating` jako `TypedChoiceField` ograniczone do `{1,2,3,4,5}` (`coerce=int`), więc wartość spoza zakresu (np. `0`, `6`, tekst) czyni formularz nieważnym zamiast trafiać do bazy. Jedyne miejsce w projekcie, gdzie ta ocena jest walidowana.

---

### `reviews/admin.py`

#### `ReviewSourceAdminForm.clean_main_categories(self)`
Metoda czyszcząca formularza admina dla pola `main_categories`. Pole jest zadeklarowane jako `forms.MultipleChoiceField` z widgetem `CheckboxSelectMultiple` (checkboxy zamiast surowego JSON-a) — ta metoda konwertuje zaznaczone wartości (zwracane przez formularz jako iterowalna kolekcja) na zwykłą listę `list(...)`, żeby zapisać je poprawnie do pola `JSONField` modelu.

#### `ReviewSourceAdmin`, `ReviewAdmin`, `UserReviewAdmin`, `ScrapeJobAdmin`
Standardowe konfiguracje `ModelAdmin` (bez własnej logiki poza deklaracjami): `ReviewSourceAdmin` używa `ReviewSourceAdminForm` i grupuje pola w dwa fieldsety (ogólne + "Zgodność z ToS": `tos_checked_at`/`tos_notes`); `ReviewAdmin` pokazuje `score` w liście i ma `autocomplete_fields` dla `product`/`source`; `UserReviewAdmin` pokazuje/filtruje po `rating`; `ScrapeJobAdmin` pokazuje status/liczbę znalezionych opinii.

---

### `reviews/views.py`

#### `ReviewViewSet` (`ReadOnlyModelViewSet`)
`GET /api/reviews/` z filtrowaniem po `product`, `sentiment`, `source` (`filterset_fields`, `django-filter`) — np. `/api/reviews/?product=1&sentiment=positive&source=2`. Bez własnej logiki poza deklaracją.

---

### `reviews/serializers.py`
`ReviewSourceSerializer` (`id, name, source_type, base_url`) i `ReviewSerializer` (zagnieżdżone źródło + pola opinii: `author, raw_text, summary, sentiment, source_url, published_at, fetched_at`). Bez własnej logiki.

---

## scrapers

Wspólny kontrakt (`scrapers/base.py`): każdy parser eksponuje `fetch(product_name, base_url, parser_config=None) -> Iterable[FetchedReview]` i **nigdy** nie zapisuje do bazy bezpośrednio — całą persystencję/deduplikację robi `scrapers/dedup.py::save_reviews` (albo, dla Crawlbase, dedykowana logika w `scrapers/tasks.py::fetch_crawlbase_reddit_post`).

#### `FetchedReview` *(dataclass, `scrapers/base.py`)*
Pola: `source_url: str`, `raw_text: str`, `author: str = ""`, `published_at: datetime | None = None`. Uniwersalny nośnik jednej "opinii" niezależnie od źródła — jedyna struktura danych przepływająca między parserem a warstwą zapisu.

---

### `scrapers/dedup.py`

#### `save_reviews(product, source: ReviewSource, fetched_reviews: Iterable[FetchedReview]) -> int`
Jedyne miejsce w projekcie zapisujące nowe `Review` z wyniku parsera (poza Crawlbase, który ma własną logikę upsert). Dla każdego `FetchedReview`: liczy hash URL-a (`Review.hash_url`), pomija jeśli `Review` z takim hashem już istnieje (trwała deduplikacja — nigdy nie nadpisuje/odświeża), w przeciwnym razie tworzy nowy rekord `Review`. Zwraca liczbę faktycznie utworzonych opinii (używaną przez taski do decyzji, czy warto wywołać `analyze_product_reviews`).

---

### `scrapers/registry.py`

#### `get_parser(parser_key: str)`
Zwraca funkcję `fetch` zarejestrowaną pod danym `parser_key` w `PARSER_REGISTRY` (słownik `{parser_key: fetch_callable}` obejmujący wszystkie źródła poza Redditem: `forum_example`, `generic_forum`, `beauty_forum_example`, `google_places`, `youtube`, `crawlbase_reddit`). Podnosi `ValueError`, jeśli klucz nie jest zarejestrowany. Wołane przez `scrapers/tasks.py::fetch_registry_reviews` do dynamicznego dispatchu bez łańcucha `if/elif`.

---

### `scrapers/reddit_scraper.py` (oficjalne API Reddita przez PRAW)

#### `_to_datetime(created_utc: float) -> datetime`
Konwertuje uniksowy timestamp z PRAW (`submission.created_utc`/`comment.created_utc`) na `datetime` ze strefą UTC.

#### `get_reddit_client() -> praw.Reddit`
Tworzy klienta PRAW z danymi uwierzytelniającymi z `settings` (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`).

#### `fetch(product_name: str, subreddits=None, limit=None, reddit_client=None)`
Przeszukuje skonfigurowane subreddity (domyślnie `settings.REDDIT_SUBREDDITS`, nadpisywalne per `ReviewSource.parser_config["subreddits"]`) pod kątem `product_name` (limit wyników: domyślnie `settings.REDDIT_SEARCH_LIMIT`). Dla każdego znalezionego posta yielduje jeden `FetchedReview` (tytuł + treść posta), a następnie — po `replace_more(limit=0)` (rozwinięcie "load more comments" bez dodatkowych zapytań za "więcej komentarzy") — po jednym `FetchedReview` na każdy komentarz najwyższego poziomu z niepustą treścią. Błędy PRAW/prawcore (`RequestException`, `ResponseException`, np. rate limit 429) łapane osobno dla wyszukiwania (przerywa całą funkcję, loguje ostrzeżenie, generator kończy się bez wyjątku) i dla komentarzy pojedynczego posta (pomija komentarze tego posta, kontynuuje z kolejnymi postami).

---

### `scrapers/generic_forum_scraper.py` (scraping HTML, selektory konfigurowalne)

#### `_polite_sleep()`
Usypia wątek na losowy czas z przedziału `[SCRAPER_MIN_DELAY_SECONDS, SCRAPER_MAX_DELAY_SECONDS]` — rate-limiting żeby nie bombardować forum zapytaniami.

#### `_get(url: str) -> requests.Response | None`
Wykonuje `GET` z nagłówkiem `User-Agent` z `settings.SCRAPER_USER_AGENT`, timeout 10 s. Zwraca `None` (z ostrzeżeniem w logu) przy dowolnym `requests.RequestException` zamiast rzucać wyjątek dalej.

#### `fetch(product_name: str, base_url: str, parser_config: dict | None = None)`
Generyczny, sterowany konfiguracją parser forum: (1) pobiera stronę wyszukiwania `{base_url}/search?q={product_name}`, wyciąga linki do wątków selektorem `parser_config["thread_link_selector"]` (domyślnie `a.thread-link[href]`); (2) dla każdego wątku pobiera stronę, wyciąga posty selektorem `post_selector` (domyślnie `div.post`), z każdego posta treść (`body_selector`) i autora (`author_selector`); yielduje `FetchedReview` per post (pomija posty bez treści). Wszystkie 5 kluczy selektorów (`thread_link_selector`, `post_selector`, `body_selector`, `author_selector`, `date_selector` — ten ostatni obecnie niewykorzystywany do parsowania daty, `published_at` zawsze `None`) mają wartości domyślne w `DEFAULT_SELECTORS`, nadpisywalne przez `parser_config`. Dzięki temu nowe forum na tym samym silniku HTML to tylko nowa konfiguracja `ReviewSource.parser_config`, bez nowego pliku Python.

---

### `scrapers/forum_scraper_example.py` (scraping HTML, przykład ze sztywnymi selektorami)

Struktura identyczna jak `generic_forum_scraper.py` (`_polite_sleep`, `_get`, `fetch`), ale selektory (`a.thread-link[href]`, `div.post`, `.post-body`, `.post-author`) są zaszyte na sztywno w kodzie zamiast czytane z `parser_config` — to celowo minimalny, ilustracyjny szkielet pokazujący oczekiwany kształt "bespoke" parsera dla forum o unikalnym układzie strony, gdzie samej konfiguracji selektorów by nie wystarczyło. `parser_config` jest przyjmowany w sygnaturze `fetch()`, ale ignorowany — zachowany wyłącznie dla spójności wywołania z `scrapers/tasks.py::fetch_registry_reviews`.

---

### `scrapers/beauty_forum_scraper.py` (scraping HTML, drugi przykład — kategoria kosmetyki)

Ta sama konstrukcja co `forum_scraper_example.py` (`_polite_sleep`, `_get`, `fetch`), ale z ilustracyjnymi selektorami dopasowanymi do układu "recenzja" zamiast "wątek forum" (`div.review-entry`, `.review-text`, `.review-author`, ścieżka wyszukiwania `/reviews/search?product=...`). Istnieje głównie jako dowód, że architektura parserów skaluje się na nowe kategorie tematyczne bez zmian w rejestrze/taskach/modelach — dodanie go wymagało tylko nowego pliku + wpisu w `PARSER_REGISTRY`.

---

### `scrapers/google_places_scraper.py` (oficjalne REST API Google, bez scrapingu)

#### `_get_json(url: str, params: dict) -> dict | None`
Wykonuje `GET` z podanymi parametrami, timeout 10 s, zwraca sparsowany JSON. Zwraca `None` przy błędzie sieciowym (`requests.RequestException`) lub niepoprawnym JSON-ie (`ValueError`), logując ostrzeżenie.

#### `_to_datetime(unix_time) -> datetime | None`
Konwertuje uniksowy timestamp recenzji Google (`review["time"]`) na `datetime` UTC; `None` dla wartości pustej/`0`.

#### `_resolve_place_id(product_name: str, api_key: str) -> str | None`
Wywołuje Google Places **Text Search** dla `product_name` i zwraca `place_id` pierwszego wyniku. Obsługuje `status` z odpowiedzi: `"ZERO_RESULTS"` → brak wyników (log info, `None`, nie błąd); dowolny inny status ≠ `"OK"` → ostrzeżenie, `None`.

#### `fetch(product_name: str, base_url: str = "", parser_config: dict | None = None)`
Pobiera recenzje Google dla miejsca pasującego do `product_name`. Jeśli `parser_config["place_id"]` jest podane, pomija Text Search i idzie od razu do **Place Details** (`fields=name,reviews`) dla tego ID — w przeciwnym razie najpierw rozwiązuje `place_id` przez `_resolve_place_id()`. Google Place Details zwraca maks. ~5 recenzji (ograniczenie samego API). Dla każdej recenzji z niepustym tekstem yielduje `FetchedReview`: `source_url` jest syntetyzowany z `place_id` + czas recenzji + zakodowany autor (`urllib.parse.quote`) — Google nie udostępnia trwałego linku per-recenzja, więc ten syntetyczny URL służy wyłącznie jako unikalny klucz do deduplikacji w `dedup.py`. Brak `settings.GOOGLE_PLACES_API_KEY` → ostrzeżenie, pusty generator (bez wyjątku).

---

### `scrapers/youtube_scraper.py` (oficjalne REST API YouTube Data v3, bez scrapingu)

#### `_get_json(url: str, params: dict) -> dict | None`
Analogicznie do wersji w `google_places_scraper.py` — `GET` + parsowanie JSON, `None` przy błędzie sieciowym/JSON.

#### `_to_datetime(published_at) -> datetime | None`
Parsuje `snippet.publishedAt` (ISO 8601, sufiks `Z`) na `datetime` przez `datetime.fromisoformat()` (po podmianie `Z` na `+00:00`); `None` przy błędzie parsowania.

#### `_search_video_ids(product_name: str, api_key: str, max_videos: int) -> list[str]`
Woła `search.list` (`part=snippet&type=video`) dla `product_name`, zwraca listę `videoId` z maks. `max_videos` wyników (pomija pozycje bez `id.videoId`).

#### `_fetch_comments(video_id: str, api_key: str, max_comments: int)`
Woła `commentThreads.list` dla danego wideo (`textFormat=plainText`, limit `max_comments`) i yielduje jeden `FetchedReview` per komentarz najwyższego poziomu z niepustym tekstem: `source_url` to prawdziwy link YouTube do komentarza (`{video_url}&lc={comment_id}`) — naturalnie unikalny per komentarz, więc różne komentarze pod tym samym filmem nigdy nie kolidują w deduplikacji. Błąd API dla danego wideo (np. wyłączone komentarze) → `_get_json` zwraca `None`, generator kończy się pusto dla tego wideo (nie przerywa pętli w `fetch()`).

#### `fetch(product_name: str, base_url: str = "", parser_config: dict | None = None)`
Główna funkcja: wyszukuje filmy (`max_videos` z `parser_config`, domyślnie 3), a dla każdego yielduje komentarze przez `_fetch_comments` (`max_comments_per_video` z `parser_config`, domyślnie 50). Brak `settings.YOUTUBE_API_KEY` → ostrzeżenie, pusty generator.

---

### `scrapers/crawlbase_reddit_scraper.py` (oficjalne API Crawlbase, jeden konkretny URL)

#### `_to_datetime(created_at) -> datetime | None`
Parsuje `post["createdAt"]` (ISO 8601) analogicznie do wersji w `youtube_scraper.py`.

#### `fetch_reddit_post(url: str) -> dict | None`
Woła Crawlbase (`GET https://api.crawlbase.com/`, `token`, `url`, `scraper=reddit-post`, timeout 30 s — dłuższy niż inne API, bo Crawlbase scrapuje stronę na żywo po swojej stronie). Zwraca `data["body"]["post"]` (surowy słownik) tylko gdy: klucz API jest ustawiony, request się powiódł (brak timeoutu/`RequestException`), status HTTP to `200`, odpowiedź parsuje się jako JSON, **i** `post["id"]` oraz (`post["permalink"]` lub `post["url"]`) są niepuste — w przeciwnym razie `None` z odpowiednim ostrzeżeniem w logu na każdym etapie. To jedyna funkcja w projekcie wywoływana **bezpośrednio** przez `scrapers/tasks.py::fetch_crawlbase_reddit_post` (z pominięciem rejestru), bo task potrzebuje pełnego surowego posta (`score`, cały payload) — nie tylko okrojonego `FetchedReview`.

#### `fetch(product_name: str, base_url: str = "", parser_config: dict | None = None)`
Wrapper zgodny z ogólnym kontraktem rejestru: czyta URL z `parser_config["url"]` (brak → ostrzeżenie, pusty generator), woła `fetch_reddit_post(url)`, i przy sukcesie yielduje **jeden** `FetchedReview` (tytuł + `selfText` jako treść, autor, `published_at` z `createdAt`). Ekstrakcja komentarzy posta świadomie nie jest zaimplementowana — realny kształt obiektu komentarza w odpowiedzi Crawlbase jest nieznany (widziane odpowiedzi miały `comments: []`).

---

### `scrapers/tasks.py` (Celery — dispatch i taski pobierania)

#### `queue_fetch(source: ReviewSource, product: Product) -> None`
Centralny punkt dispatchu: jeśli `source.source_type == REDDIT` → `fetch_reddit_reviews.delay(product.id, source.id)`; w przeciwnym razie (`FORUM`, `REVIEW_SITE`, `OTHER`) → `fetch_registry_reviews.delay(product.id, source.id)` (wszystkie rozwiązują parser przez `scrapers.registry`, więc dzielą jeden task). Wołane bezpośrednio przez `refresh_recent_products` (Celery Beat) i `ProductViewSet.refresh_reviews` (ręczna akcja API) — a także, per-źródło wewnątrz pętli, przez `queue_live_search_fetch` poniżej, tam jednak owinięte w `try/except`, bo to jedyny wywołujący, który nie może pozwolić sobie na propagację wyjątku.

#### `queue_live_search_fetch(product: Product) -> None`
Triggerowane z `products/web_views.py::product_search` przy każdym niepustym wyszukiwaniu. Iteruje po aktywnych `ReviewSource` z `parser_key` w `LIVE_SEARCH_PARSER_KEYS` (`{"google_places", "youtube"}` — świadomie **bez** Reddita/forów, które zostają za jawną akcją `refresh-reviews`, i bez Crawlbase, które wymaga konkretnego URL-a, nie nazwy produktu), filtruje po `source.applies_to(product.category.main_category)`, i dla każdego pasującego woła `queue_fetch(source, product)` **w bloku `try/except`** — błąd kolejkowania (np. broker Celery nieosiągalny) jest logowany (`logger.exception`) i połykany, nigdy nie propaguje się dalej. To jedyne miejsce w projekcie, gdzie fetch odpala się pasywnie przy zwykłym przeglądaniu strony (nie jawnej akcji), więc musi być odporne na awarię infrastruktury.

#### `PARSER_KEY_TO_PROVIDER` (dict)
Mapuje `ReviewSource.parser_key` → `APIUsageLog.Provider` dla źródeł objętych monitoringiem kosztów: `{"youtube": YOUTUBE, "google_places": GOOGLE_PLACES, "crawlbase_reddit": CRAWLBASE}`. Fora HTML (`forum_example`/`generic_forum`/`beauty_forum_example`) świadomie nie są w mapie — nie mają limitów/kosztów w rozumieniu tego monitoringu.

#### `fetch_reddit_reviews(product_id: int, source_id: int)` *(Celery task)*
Pobiera opinie z Reddita dla produktu przez wskazane `ReviewSource`. Tworzy `ScrapeJob` (status `RUNNING`), czyta `subreddits` z `source.parser_config` (fallback na domyślne z `reddit_scraper.fetch` jeśli nie skonfigurowano), woła `reddit_scraper.fetch(...)`, zapisuje wynik przez `dedup.save_reviews` — owinięte w `track_api_usage(APIUsageLog.Provider.REDDIT, "fetch_reviews", product_id=product.id)` (patrz `analysis/usage.py`; owinięte wokół `save_reviews`, nie samego `fetch()`, bo `fetch()` jest leniwym generatorem i realne zapytania HTTP wykonują się dopiero przy iteracji wewnątrz `save_reviews`). Sukces → `ScrapeJob` na `SUCCESS` z liczbą znalezionych opinii; jeśli powstały nowe opinie, kolejkuje `analysis.tasks.analyze_product_reviews.delay(product_id)` (import wewnątrz funkcji — unika zależności cyklicznej na poziomie modułu między `scrapers` i `analysis`). Dowolny wyjątek w trakcie → `ScrapeJob` na `FAILED` z treścią błędu, wyjątek nie propaguje się dalej (task kończy się "sukcesem" z punktu widzenia Celery, błąd jest zarejestrowany w danych).

#### `fetch_registry_reviews(product_id: int, source_id: int)` *(Celery task)*
Analogicznie do `fetch_reddit_reviews`, ale dla dowolnego źródła rozwiązywanego przez `scrapers.registry.get_parser(source.parser_key)` — forum (scraping HTML) lub oficjalne API (Google Places, YouTube) na równych prawach. Wywołuje parser z `base_url=source.base_url, parser_config=source.parser_config`. Jeśli `PARSER_KEY_TO_PROVIDER.get(source.parser_key)` zwróci provider — `save_reviews(...)` owinięte w `track_api_usage(provider, "fetch_reviews", product_id=product.id)`; w przeciwnym razie (fora HTML) bez logowania kosztów. Ta sama logika `ScrapeJob`/obsługi błędów/triggerowania analizy co wyżej.

#### `refresh_recent_products()` *(Celery task, wejście dla Celery Beat)*
Cykliczne zadanie (harmonogram tworzony automatycznie przez migrację `scrapers.0001_periodic_refresh_schedule`): dla każdego `Product` utworzonego w ciągu ostatnich `settings.REVIEW_REFRESH_WINDOW_DAYS` dni i każdego aktywnego `ReviewSource`, którego `applies_to()` pasuje do kategorii produktu, woła `queue_fetch(source, product)` — czyli odświeża opinie dla świeżo dodanych produktów bez ręcznej interwencji.

#### `fetch_crawlbase_reddit_post(product_id: int, source_id: int, url: str)` *(Celery task)*
Pobiera **jeden konkretny** URL posta z Reddita przez Crawlbase. Nie jest częścią dispatchu `queue_fetch()` — wywoływane bezpośrednio z `ProductViewSet.fetch_crawlbase_reddit_post` z jawnym URL-em. Logika:
1. Liczy `url_hash = Review.hash_url(url)`. Jeśli istniejący `Review` z tym hashem jest świeższy niż `CRAWLBASE_FRESHNESS_WINDOW` (24h) — tworzy `ScrapeJob` ze statusem `SUCCESS`, `reviews_found=0`, komunikatem o pominięciu, i **kończy bez wywołania Crawlbase** (oszczędność limitu/kosztu API — główny sens tego cache'a).
2. W przeciwnym razie woła `fetch_reddit_post(url)` **bezpośrednio** (nie przez rejestr — patrz uzasadnienie w docstringu funkcji: rejestr zwróciłby tylko `FetchedReview`, bez `score`/pełnego payloadu, a wywołanie obu oznaczałoby dwa zapytania do Crawlbase za jeden job).
2b. Wywołanie `fetch_reddit_post(url)` owinięte w `track_api_usage(APIUsageLog.Provider.CRAWLBASE, "fetch_reddit_post", product_id=product.id)` — tu wprost wokół wywołania (nie generator, prosty `with`).
3. Sukces → `Review.objects.update_or_create(source_url_hash=url_hash, defaults={...})` — **upsert**, nie insert-only jak `dedup.save_reviews`, bo to jedyny task, który musi *odświeżyć* istniejący wiersz, gdy jest przestarzały. `defaults` jawnie resetuje `sentiment` do `None` (żeby `analyze_product_reviews` przeklasyfikował odświeżoną treść) i jawnie ustawia `fetched_at=timezone.now()` (bo `auto_now_add` odświeża się tylko przy INSERT, nie przy UPDATE — bez tego pole nigdy by nie awansowało i każde kolejne uruchomienie widziałoby wiersz jako wciąż przestarzały).
4. Standardowe bookkeeping `ScrapeJob` (`SUCCESS`/`FAILED`) i trigger `analyze_product_reviews.delay()` przy powstaniu/aktualizacji rekordu.

---

## analysis

### `analysis/models.py`

#### `SummaryFlag`
Zgłoszenie "to podsumowanie AI jest nietrafne" od zalogowanego użytkownika, dla `product.pros_summary` albo `product.cons_summary` (`target`, `TextChoices` `PROS`/`CONS`). `unique_together=("product","user","target")` — `products.web_views.flag_summary` woła `get_or_create`, więc powtórne zgłoszenie tego samego jest no-opem, nie duplikatem. Widoczne wyłącznie w `/admin/analysis/summaryflag/` (`SummaryFlagAdmin`) — bez osobnego workflow moderacji na tym etapie, wzorem `APIUsageLog`.

#### `APIUsageLog` (przemianowany z `AIUsageLog`)
Ujednolicony log kosztów dla **wszystkich** zewnętrznych API projektu, nie tylko Anthropic. Pola: `provider` (`TextChoices`: `anthropic`/`youtube`/`google_places`/`crawlbase`/`reddit`), `action` (nazwa operacji, np. `"fetch_reviews"`, `"classify_sentiment"` — było `task_name`), `product` (FK nullable — nie każde wywołanie dotyczy jednego produktu), `units` (`PositiveIntegerField(default=1)` — liczba zużytych "jednostek" dla providerów bez tokenów, np. 1 na wywołanie `fetch()`), `input_tokens`/`output_tokens` (nullable — tylko Anthropic je wypełnia), `created_at`. Zapisywany wyłącznie przez `analysis/usage.py::log_api_usage`, nigdy bezpośrednio przez wywołujących.

---

### `analysis/usage.py` (wspólny helper monitoringu kosztów)

#### `log_api_usage(provider, action, *, product_id=None, units=1, input_tokens=None, output_tokens=None) -> None`
Jedyne miejsce, które tworzy wiersze `APIUsageLog`. Best-effort: `try/except` wokół `APIUsageLog.objects.create(...)` — błąd zapisu jest logowany (`logger.exception`) i połykany, nigdy nie przerywa wywołującego (logowanie kosztów nie może zepsuć samego fetchu/klasyfikacji). Po udanym zapisie zawsze woła `_check_daily_budget(provider)`.

#### `track_api_usage(provider, action, *, product_id=None, units=1)` *(context manager)*
`try: yield finally: log_api_usage(...)` — loguje **niezależnie od tego, czy blok rzucił wyjątek**, bo zapytanie do zewnętrznego API zużywa limit/kwotę nawet przy niepowodzeniu (np. 4xx z YouTube nadal liczy się do dziennego limitu Google). Używane w `scrapers/tasks.py` do owinięcia trzech miejsc pobierania (patrz tam) — musi owijać *konsumpcję* generatora (`save_reviews(...)`), nie samo wywołanie leniwego `fetch()`, inaczej nic by nie zmierzyło (realne żądania HTTP wykonują się dopiero przy iteracji).

#### `_check_daily_budget(provider) -> None`
Po każdym zapisie sumuje `APIUsageLog` danego providera z ostatnich 24h (`units` dla nie-Anthropic, `input_tokens + output_tokens` dla Anthropic) i porównuje z `settings.DAILY_PROVIDER_BUDGETS.get(provider)`. Brak wpisu / `None` / `0` → brak alertu (budżet niekonfigurowany = wyłączony, nie "zero tolerancji"). Po przekroczeniu: `logger.critical(...)` z jawnym `# TODO: podłącz Slack/Sentry` — pierwszy krok monitoringu, bez integracji z zewnętrznym kanałem alertowym.

---

### `analysis/admin.py`

#### `_usage_summary(since) -> list[dict]`
Buduje podsumowanie zużycia API od danego momentu (`timezone.now() - timedelta(...)`): `APIUsageLog.objects.filter(created_at__gte=since).values("provider").annotate(...)` sumujące `units` oraz `input_tokens`/`output_tokens` per provider. Używane przez `APIUsageLogAdmin.changelist_view` do zbudowania okien 24h i 7 dni.

#### `APIUsageLogAdmin.changelist_view(self, request, extra_context=None)`
Nadpisany widok listy w `/admin/analysis/apiusagelog/`: dolicza `usage_summary_24h`/`usage_summary_7d` (przez `_usage_summary`) do `extra_context` przed wywołaniem `super().changelist_view(...)`. Renderowane przez `templates/admin/analysis/apiusagelog/change_list.html` (rozszerza `admin/change_list.html`, wstrzykuje tabelę podsumowania nad standardową listą przez nadpisany blok `date_hierarchy`).

#### `SummaryFlagAdmin` (`ModelAdmin`)
Standardowa konfiguracja bez własnej logiki: `list_display=("product","user","target","created_at")`, `list_filter=("target",)` — jedyny sposób przeglądania zgłoszeń "nietrafne podsumowanie" na tym etapie (patrz `SummaryFlag` wyżej).

---

### `analysis/prompts.py`

#### `category_label(main_category: str) -> str`
Zwraca angielską frazę opisową kategorii do promptów Claude (np. `"an electronics product"`) z `CATEGORY_LABELS`; nieznana/pusta wartość → fallback na `"electronics"`.

#### `category_aspect_hints(main_category: str) -> str`
Zwraca frazę wskazującą, na jakie aspekty produktu zwrócić uwagę przy podsumowywaniu opinii (np. dla kosmetyków: `"skin/hair reaction, scent, allergies, and overall effectiveness"`) z `CATEGORY_ASPECT_HINTS`; ten sam fallback na `"electronics"`.

---

### `analysis/retry.py` (wspólny retry/backoff dla wszystkich wywołań Anthropic)

#### `_retry_delay(exc: Exception | None, attempt: int) -> float`
Wylicza opóźnienie przed kolejną próbą: jeśli wyjątek ma `.response.headers["retry-after"]` (parsowalne na liczbę) — używa tej wartości wprost; inaczej exponential backoff z jitterem (`min(BASE_DELAY_SECONDS * 2**(attempt-1), MAX_DELAY_SECONDS) + losowe 0–1s`).

#### `_log_usage(response, *, task_name: str, product_id: int | None) -> None`
Po udanym wywołaniu deleguje do `analysis/usage.py::log_api_usage(APIUsageLog.Provider.ANTHROPIC, task_name, product_id=product_id, input_tokens=..., output_tokens=...)` z `response.usage` — jedno wspólne miejsce logowania kosztów dla wszystkich providerów, nie zduplikowana logika. Nic nie robi, jeśli `response.usage` jest `None`. Best-effort dalej w głąb (`log_api_usage` łapie błędy zapisu) — logowanie kosztów nie może zepsuć samej klasyfikacji/podsumowania.

#### `PermanentAPIError(Exception)`
Podnoszony przez `call_with_retry` dla błędu nie do naprawienia retry (zły klucz/model/zapytanie). `classify_sentiment`/`summarize_pros_cons` łapią go lokalnie i zwracają `None` jak przy każdym innym niepowodzeniu (nie muszą go odróżniać od błędu przejściowego); `ask_about_product` **nie łapie** — pozwala mu dojść do `generate_product_ai_overview`, jedynego miejsca, które faktycznie potrzebuje rozróżnienia (patrz niżej).

#### `call_with_retry(make_request: Callable[[], Message], *, task_name: str, product_id: int | None = None)`
Centralny punkt obsługi błędów dla **wszystkich trzech** wywołań Anthropic w projekcie. `make_request` to bezargumentowy callable wykonujący `client.messages.create(...)` — helper nic nie wie o kształcie promptu/narzędzi, tylko o warstwie transportowej. Do 3 prób:
- `anthropic.APIStatusError` ze `status_code` w `{400,401,403,404,413,422}` → log błędu konfiguracyjnego + **`raise PermanentAPIError`** natychmiast, bez próby ponowienia.
- `status_code` w `{429,500,529}` (rate limit / błąd serwera) → retry z backoffem (`_retry_delay`); po wyczerpaniu 3 prób — `None`.
- `anthropic.APIConnectionError` (błąd sieciowy) → retry tą samą ścieżką.
- inny/nierozpoznany błąd → traktowany konserwatywnie jak wyczerpanie prób (`None`), nie zgadywany jako bezpieczny do ponowienia.

Sukces → `_log_usage(...)` + zwraca surowy obiekt odpowiedzi (`Message`) — ekstrakcję treści/tool-use robi wywołujący.

---

### `analysis/sentiment.py`

#### `_client() -> anthropic.Anthropic`
Buduje klienta Anthropic z `settings.ANTHROPIC_API_KEY`, `timeout=30.0` i **`max_retries=0`** (cała logika retry idzie przez `analysis/retry.py::call_with_retry`, nie przez wbudowaną, nieprzezroczystą logikę SDK). Jeśli `settings.ANTHROPIC_WORKSPACE_ID` jest ustawione, dołącza nagłówek `anthropic-workspace-id` (wymagany przez "identity-linked"/workspace-scoped klucze API). Współdzielone przez `summarizer.py` i `overview.py` (import `from .sentiment import _client`).

#### `_extract_tool_input(response, tool_name: str) -> dict | None`
Szuka w `response.content` bloku typu `tool_use` o podanej nazwie i zwraca jego `.input` (gotowy dict, bez `json.loads`) — `None`, jeśli nie znaleziono. Współdzielone przez `summarize_pros_cons` (import `from .sentiment import _extract_tool_input`).

#### `classify_sentiment(text: str, main_category: str = "electronics", product_id: int | None = None) -> str | None`
Klasyfikuje pojedynczą opinię jako `"positive"`/`"neutral"`/`"negative"`. Wymusza strukturę odpowiedzi przez **tool use** (`SENTIMENT_TOOL`, `tool_choice` wskazujący na to narzędzie) zamiast prosić model o "czysty JSON w tekście" — eliminuje całą klasę błędów parsowania. Woła `call_with_retry(...)`; `PermanentAPIError` łapany lokalnie i zwraca `None` (bez rozróżniania od błędu przejściowego — `analyze_product_reviews` i tak nie próbuje częściej niż raz na `SENTIMENT_RETRY_COOLDOWN_MINUTES`, niezależnie od przyczyny niepowodzenia). `product_id` (opcjonalny, nowy parametr — nie łamie istniejących wywołań bez niego) trafia do `APIUsageLog` przy udanym wywołaniu.

---

### `analysis/summarizer.py`

#### `summarize_pros_cons(product_name: str, review_texts: list[str], main_category: str = "electronics", product_id: int | None = None) -> dict | None`
Generuje zbiorcze podsumowanie zalet/wad produktu na podstawie próbki zebranych opinii (maks. `MAX_REVIEWS_IN_PROMPT`=40 tekstów, każdy przycięty do `MAX_CHARS_PER_REVIEW`=500 znaków). Prompt zawiera kontekst kategorii (`category_label`) i podpowiedź, na jakie aspekty zwrócić uwagę (`category_aspect_hints`). Wymusza strukturę odpowiedzi przez tool use (`SUMMARY_TOOL`), tak jak `classify_sentiment`. Zwraca `{"pros": str, "cons": str}` lub `None`, gdy: brak klucza API, brak opinii (`review_texts` puste), błąd trwały/przejściowy przez `call_with_retry`, lub odpowiedź nie zawiera obu wymaganych kluczy.

---

### `analysis/overview.py`

#### `ask_about_product(brand: str, model_name: str, main_category: str, product_id: int | None = None) -> str | None`
Zadaje Claude bezpośrednie pytanie "co wiesz o tym produkcie" — **nie** analizuje zebranych opinii, tylko odpytuje wiedzę własną modelu, jak przy bezpośrednim pytaniu w czacie. Zostaje wolnym tekstem (bez tool use — nie ma sensu wymuszać struktury na swobodnym opisie). Woła `call_with_retry(...)`, ale **w przeciwieństwie** do `classify_sentiment`/`summarize_pros_cons` **nie łapie `PermanentAPIError`** — pozwala mu propagować do jedynego wywołującego, `generate_product_ai_overview`, które musi rozróżnić błąd trwały od przejściowego (patrz niżej). Zwraca `None` tylko gdy `call_with_retry` wyczerpało próby błędu przejściowego.

---

### `analysis/tasks.py`

#### `analyze_product_reviews(product_id: int)` *(Celery task)*
Dwuetapowa analiza dla produktu: (1) dla opinii z `sentiment IS NULL`, których `sentiment_last_attempt_at` jest puste **lub** starsze niż `settings.SENTIMENT_RETRY_COOLDOWN_MINUTES` — woła `classify_sentiment(...)`, i **niezależnie od wyniku** ustawia `sentiment_last_attempt_at = now()` (przy sukcesie razem z `sentiment` w jednym zapisie); dzięki temu nieudana klasyfikacja nie jest próbowana ponownie częściej niż raz na cooldown, ale nigdy nie zostaje trwale pominięta. (2) Na podstawie **wszystkich** tekstów opinii produktu woła `summarize_pros_cons(...)` i zapisuje `pros_summary`/`cons_summary`/`summary_updated_at` (pomija zapis, jeśli `None`). Triggerowane automatycznie po każdym udanym pobraniu nowych opinii.

#### `generate_product_ai_overview(product_id: int)` *(Celery task)*
Woła `ask_about_product(...)` i rozróżnia **dlaczego** nie dostał odpowiedzi:
- **`PermanentAPIError`** (zły klucz/model/zapytanie — retry tego nie naprawi) → zapisuje `FALLBACK_OVERVIEW_MESSAGE` do `Product.ai_overview`, żeby strona przestała pokazywać "generowanie w toku" i zapętlać auto-odświeżanie.
- **`None`** (błąd przejściowy, wyczerpane próby) → zostawia `ai_overview` puste i **zeruje `ai_overview_requested_at`** (`Product.objects.filter(...).update(...)`) — dzięki temu kolejna wizyta na stronie produktu (`product_detail`/`queue_ai_overview`) spróbuje ponownie, zamiast utknąć w stanie "już zażądano" na zawsze.

#### `queue_ai_overview(product: Product) -> None`
Bez zmian względem poprzedniej wersji — kolejkuje `generate_product_ai_overview`, ustawia `ai_overview_requested_at` tylko po udanym `.delay()`, błąd brokera łapany i logowany.
