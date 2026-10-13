# Telegram Corner & Goals Report

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

1. Utwórz konto Sportmonks i token API. Darmowy plan obejmuje Danish Superliga (`league_id=271`).
2. Utwórz bota przez `@BotFather`, rozpocznij z nim rozmowę i ustal `chat_id`.
3. Skopiuj `.env.example` do własnych zmiennych środowiskowych.
4. Uruchom:

```bash
python3 app.py
```

Skrypt korzysta wyłącznie ze standardowej biblioteki Pythona.

## GitHub Actions

Do codziennej wysyłki bez włączonego komputera:

1. Wypchnij repozytorium na GitHub.
2. W ustawieniach repozytorium dodaj sekrety:
   - `SPORTMONKS_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Opcjonalnie dodaj zmienną repozytorium `LEAGUE_IDS`. Dla darmowego planu zostaw `271`.
4. Uruchom workflow ręcznie przez `Actions`, aby wykonać pierwszy test.

Workflow jest ustawiony na codziennie o 07:00 czasu polskiego zimą. Godziny w harmonogramie GitHub są oparte na UTC.

## Ograniczenia darmowego API

Darmowy plan Sportmonks obejmuje Danish Superliga i Scottish Premiership. Ta wersja używa wyłącznie Danish Superliga (`271`) i pobiera bieżący sezon, historię, rzuty rożne, gole oraz H2H.
