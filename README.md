MVP wysyła raz dziennie na Telegram listę najciekawszych meczów. Raport pokazuje trzy osobne perspektywy:

| Statystyka | Gospodarze | Goście | H2H |
|---|---:|---:|---:|
| Rzuty rożne | średnia z meczów u siebie | średnia z meczów na wyjeździe | średnia z ostatnich meczów bezpośrednich |
| Gole | średnia gospodarzy | średnia gości | średnia H2H |

Domyślnie wysyłane są tylko mecze, dla których model wyliczy jednocześnie:

- więcej niż 10 rzutów rożnych,
- więcej niż 2,5 gola.

Model używa głównie aktualnych statystyk home/away. H2H ma wagę pomocniczą 20%, żeby stare bezpośrednie mecze nie zdominowały bieżącej formy.

## Uruchomienie lokalne

1. Utwórz konto 5DollarFootballAPI i wygeneruj darmowy klucz API. Darmowy plan obejmuje pięć największych lig europejskich.
2. Utwórz darmowy token Sportmonks, jeśli chcesz dodać Danię i Szkocję. Ten plan obejmuje Danish Superliga (271) i Scottish Premiership (501).
3. Utwórz bota przez `@BotFather`, rozpocznij z nim rozmowę i ustal `chat_id`.
4. Skopiuj `.env.example` do własnych zmiennych środowiskowych.
5. Uruchom:

```bash
python3 app.py
```

Skrypt korzysta wyłącznie ze standardowej biblioteki Pythona.

## GitHub Actions

Do codziennej wysyłki bez włączonego komputera:

1. Wypchnij repozytorium na GitHub.
2. W ustawieniach repozytorium dodaj sekrety:
    - `FIVEDOLLARFOOTBALL_API_KEY`
   - `SPORTMONKS_TOKEN` (opcjonalnie; włącza Danię i Szkocję)
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Pozostaw `LEAGUE_IDS` z pięcioma domyślnymi identyfikatorami lig z `.env.example`. `SPORTMONKS_LEAGUE_IDS` zawiera Danię i Szkocję.
4. Uruchom workflow ręcznie przez `Actions`, aby wykonać pierwszy test.

Przy ręcznym uruchomieniu można zaznaczyć `test_today`, aby sprawdzić Telegram także po rozpoczęciu meczów. Ten tryb obejmuje wszystkie mecze z dzisiejszej daty; automatyczny raport nadal wybiera wyłącznie spotkania jeszcze nierozpoczęte.

Workflow wysyła raport codziennie o 06:00 czasu polskiego. GitHub Actions używa UTC, dlatego workflow uwzględnia czas letni i zimowy, a skrypt pomija dodatkowe wyzwolenie kontrolne.

## Ograniczenia darmowego API

Darmowy plan 5DollarFootballAPI obejmuje Premier League, La Ligę, Serie A, Bundesligę i Ligue 1. Zwraca bieżące mecze, gole, rzuty rożne i kartki. Historia darmowego planu obejmuje ostatnie 3 miesiące, więc raport używa ostatnich dostępnych meczów drużyn. H2H jest liczone lokalnie z historii spotkań.

Jeśli `SPORTMONKS_TOKEN` nie jest ustawiony, raport działa nadal, ale pomija Danię i Szkocję. Wiadomość Telegram zawiera oznaczenie źródeł danych wymagane przez dostawców.
