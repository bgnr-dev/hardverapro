# hardverapro-figyelo

Egyszerű cronjob, ami az új [hardverapro.hu](https://hardverapro.hu) hirdetéseket figyeli.

Headless Chrome (Selenium) kell — a sima HTTP kérést a kereső indexre dobja cookie-consent nélkül.

## Telepítés

```bash
git clone <repo-url>.git
cd hardverapro-figyelo
pip install -r requirements.txt
cp keywords.example.txt keywords.txt
```

Kell még: Chrome/Chromium + ChromeDriver (pl. Arch: `sudo pacman -S chromium chromedriver`).

Szerkeszd a `keywords.txt`-et a saját kereséseiddel.

> Ha a `pip` „externally-managed-environment” hibát dob (pl. újabb Arch/Debian), telepítsd a csomagokat a disztródból, vagy használj `pip install --user -r requirements.txt`-et.

## keywords.txt

Egy szabály soronként. A `#`-tel kezdődő sorok és az üres sorok kimaradnak.

```text
# kulcsszó (minden szónak szerepelnie kell a címben)
5950x [90k]
x570
Thinkpad T16 -FHD -4K [400e]

# kategória-URL + kulcsszó is megy
https://hardverapro.hu/aprok/hardver/videokartya/index.html 4070 -Ti [250e]
```

- `-szó` → kihagyja azokat a címeket, amikben ez a szó szerepel
- `[65000]`, `[65 000]`, `[80k]`, `[80e]` → max ár (Ft); `k`/`e` = ezer (80k = 80e = 80000)
- az előresorolt hirdetések is bekerülnek, ha a cím stimmel (az outputon „kiemelt” a dátum helyett)
- minden megadott kulcsszónak szerepelnie kell a címben (pl. `x570` nem egyezik az `RX5700`-zal)

## Használat

```bash
python3 scraper.py          # találatok számát és az új hirdetések mutatja
python3 scraper.py -a       # minden létező találat megjelenítése 
python3 scraper.py -m       # monitor mód: csak akkor ír, ha van új (cronhoz ajánlott)
```

Kilépési kód: `0` = nincs új, `1` = van új hirdetés.  
Az első futtatás az aktuális találatokat újnak tekinti. Az állapot a `state.json`-ban van (gitignore-olt).

### Példa kimenet

**Van új hirdetés:**

```text
[22:40:08] Hardverapró figyelő
[22:40:09] '5950x'… ok, 0 találat
[22:40:11] 'x570'… 4 új (4 találat)
    ma 19:55 |       45 000 Ft | Gigabyte aorus x570 | https://hardverapro.hu/apro/gigabyte_aorus_x570/friss.html
    ma 19:54 |       35 000 Ft | Asus TUF gaming X570 plus II | https://hardverapro.hu/apro/asus_tuf_gaming_x570_plus_ii/friss.html
    ma 19:01 |       69 999 Ft | MSI MAG X570 TOMAHAWK WIFI eladó! GARANCIA/SZÁMLA … | https://hardverapro.hu/apro/…
    ma 18:52 |       47 000 Ft | MSI X570-A PRO Alaplap | https://hardverapro.hu/apro/msi_x570-a_pro_alaplap_7/friss.html
[22:40:11] Kész — van új hirdetés
```

**Nincs új:**

```text
[22:42:14] Hardverapró figyelő
[22:42:15] '5950x'… ok, 0 találat
[22:42:17] 'x570'… ok, 4 találat
[22:42:17] Kész — nincs új hirdetés
```

**Monitor mód (`-m`) — csak újnál ír:**

```text
[22:45:01] Új hirdetések:
[22:45:01] 'x570' — 1 új
    ma 22:40 |       42 000 Ft | ASRock X570 Phantom Gaming 4 | https://hardverapro.hu/apro/…
```

Ha nincs új: *nincs kimenet*.

## Cron

```bash
crontab -e
```

```cron
*/45 * * * * cd /eleresi/ut/hardverapro-figyelo && python3 scraper.py -m >> figyelo.log 2>&1
```

E-mail csak akkor, ha van kimenet:

```cron
*/45 * * * * cd /eleresi/ut/hardverapro-figyelo && OUTPUT=$(python3 scraper.py -m) && [ -n "$OUTPUT" ] && echo "$OUTPUT" | mail -s "hardverapro: új hirdetés" te@pelda.hu
```

## Fájlok

| Fájl | Szerep |
|------|--------|
| `scraper.py` | A figyelő |
| `keywords.example.txt` | Sablon |
| `keywords.txt` | Saját szabályok (helyi, gitignore) |
| `state.json` | Látott hirdetés-azonosítók (első futáskor jön létre) |
| `requirements.txt` | `selenium`, `beautifulsoup4` |

## Megjegyzések

- Ha az oldal HTML-je változik, a `parse_ads()` CSS szelektorait kell frissíteni.
- Nem hivatalos eszköz — saját felelősségre; tartsd be a hardverapro.hu feltételeit és a rate limitet.

## Licenc

MIT
