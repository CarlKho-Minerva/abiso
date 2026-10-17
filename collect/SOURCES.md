# Sources: every issuer that affects a household in Barangay Basak

Checked and captured 2026-10-03/04 (PH), logged out, human pace. `page` in `data/raw/posts.jsonl` names the source. `data/raw/capture_log.jsonl` keeps one line per source per run: where it landed, why it stopped, and the Page intro text quoted below.

## What is in posts.jsonl (167 posts)

| Issuer | `page` | Posts | Posted | Disruption-like | How it was read |
|---|---|---|---|---|---|
| MECO (power) | `mecomactan.com` | 60 | 2026-07-30 to 10-02 | 60 | WordPress feed of the site's daily "OUTAGE NOTICES" posts; the notices are images (253, all downloaded) |
| MCWD (water) | `mcwd.gov.ph` | 60 | cards dated 2026-06-02 to 10-04 | 60 | "Water Service Interruptions" cards: 45 newest mentioning Lapu-Lapu, Mactan or Cordova, then the 15 newest elsewhere |
| MCWD | `metrocebuwater` (Facebook) | 1 | 2026-10-04 | 1 | logged-out Page |
| DepEd Lapu-Lapu City Division | `depedlapulapu.net.ph` | 20 | 2026-09-24 to 10-02 | 0 | issuance titles (memos, advisories) from the site's download feed |
| DepEd Lapu-Lapu City Division | `DepEdTayoLapulapuCity` (Facebook) | 1 | 2026-10-03 | 0 | logged-out Page |
| City government | `LLCInformationOffice` (Facebook) | 4 | 2026-10-03 | 1 (CTMS traffic advisory, Oct 11) | logged-out Page + permalinks |
| City government | `lapulapucity.gov.ph` | 14 | no dates (2023 items) | 0 | site news pages |
| NGCP (grid) | `NGCPph` (Facebook) | 1 | 2026-10-03 | 1 (power situation outlook) | logged-out Page |
| PAGASA Visayas PRSD (weather) | `pagasa.dost.gov.ph` | 2 | 2026-10-04 | 2 (thunderstorm advisory + watch) | current warnings tab; the site keeps no archive |
| Barangay Basak (SK) | `BasakSKCouncil` (Facebook) | 4 | 2026-10-01 to 10-03 | 0 | logged-out Page |

"Disruption-like" is my reading for planning only, not a label. The reference labels (label/) are the ground truth.

Set aside in `data/raw/excluded.jsonl` (5 posts, kept, never labelled): `LLCPIO` and `lapulapucity` (see below).

## How each source was confirmed official

| Issuer | URL | Evidence |
|---|---|---|
| MECO | https://mecomactan.com/ | Footer "Copyright 2012 - 2026 Mactan Electric Company Inc.", office address Sangi Road, Pajo, Lapu-Lapu City; the posts are MECO-branded notices with ticket numbers |
| MECO | https://www.facebook.com/mecomactan | Linked from mecomactan.com. **Not captured: logged out it redirects straight to /login** (twice, 2026-10-04) |
| MCWD | https://www.mcwd.gov.ph/ | `.gov.ph` domain (government only); MCWD is a GOCC (FOI portal lists it as "metcebwd") |
| MCWD | https://www.facebook.com/metrocebuwater | Page intro: "A Government Owned and Controlled Corporation (GOCC)", links mcwd.gov.ph and customercare@mcwd.gov.ph, 170K followers |
| DepEd division | https://depedlapulapu.net.ph/ | Linked from DepEd Region VII's list of schools division offices (https://region7.deped.gov.ph/schools-division-offices/), next to deped.lapulapu@deped.gov.ph |
| DepEd division | https://www.facebook.com/DepEdTayoLapulapuCity | Linked from depedlapulapu.net.ph; intro "implemented under the DepEd - Division of Lapu-Lapu City", category Government organization, BM Dimataga St address |
| DepEd division | https://www.facebook.com/DepEDLLC | Older division Page (same Dimataga address in search listings). **Not captured: logged out it redirects to /login** |
| City government | https://www.facebook.com/LLCInformationOffice | Intro: "the official Facebook page ... managed under the leadership of Mayor Ma. Cynthia King-Chan & City Council", City Hall address, links lapulapucity.gov.ph |
| City government | https://lapulapucity.gov.ph/ | `.gov.ph` domain. Stale: newest items are from 2023 (previous administration); most item images now 404 |
| NGCP | https://www.facebook.com/NGCPph | Intro: "Transmission service provider and system operator of the Philippine power grid", links ngcp.ph and corpcomm@ngcp.ph, 1M followers. https://www.ngcp.ph/ itself answered 403 (curl) and "Page Not Found" (Dia) from here |
| PAGASA | https://www.pagasa.dost.gov.ph/regional-forecast/visprsd | `.gov.ph`, DOST agency; Visayas PAGASA Regional Services Division |
| Barangay Basak | https://www.facebook.com/BasakSKCouncil | Intro: "The official page of SANGGUNIANG KABATAAN OF BARANGAY BASAK term 2023-2025", Basak address. The SK is an elected barangay body (RA 10742), but it is the youth council, not the barangay council |

**Not found:** a Page or site of Barangay Basak's own council. Searches returned only the SK Page and community pages ("Basak Lapulapu City Cebu", not official). The city site's barangay page (lapulapucity.gov.ph/barangay/5) returns 404.

**Water provider for Basak: MCWD.** MCWD's own cards name Basak, Lapu-Lapu City as an affected area (e.g. the 2026-08-11 Gabi, Cordova mainline repair: "AA: Agus, Marigondon, Basak, Lapu-Lapu City"). MCWD buys bulk water for Mactan from private plants (Vivant-Cordova; Mactan Rock Industries per news reports), so a plant shut-off reaches Basak only through an MCWD notice.

## Source quality: what to trust and what to watch

- **LLCPIO looks hijacked or abandoned.** The 192K-follower "Public Information Office of the Lapu-Lapu City Government Official Facebook Page" shows only Thai-language drama episodes (2025-12-08, 2025-12-12, 2026-08-06). Its 4 posts are in `excluded.jsonl`. Worth telling the city.
- **`lapulapucity` (137K) is dormant:** its newest visible post is from 2019. Excluded.
- **Cross-issuer chains are real and current.** 2026-10-04: MCWD "All pumps in Lapu-Lapu City are currently not operational due to a power interruption from MECO / 4Oct, 7am-12nn / AA: Whole area of Lapu-Lapu City", and the Vivant-Cordova plant shut off for the same reason. On 2026-10-03 RDC-7 declared a power emergency.
- **Place names collide across cities.** MCWD serves all of Metro Cebu: "Basak San Nicolas" and "Babag" are in Cebu City, and Mandaue has its own "Basak" and "Looc". That is why MCWD selection keys on "Lapu-Lapu/Mactan/Cordova" and not on barangay names, and why 15 non-Lapu-Lapu cards are in the set as location negatives.
- **Styled Unicode.** Facebook posts use math-bold letters (𝐓𝐑𝐀𝐅𝐅𝐈𝐂 𝐀𝐃𝐕𝐈𝐒𝐎𝐑𝐘); plain keyword search misses them. Normalise with NFKC before matching.
- **Times.** Facebook `posted_at` comes from the page's own epoch `creation_time` (exact). MECO and DepEd come from WordPress `date_gmt` (UTC by WordPress's definition). MECO's site reports `date == date_gmt`, i.e. its clock is UTC, so 23:56 UTC = 07:56 PH the next morning: the daily post comes after the day it covers. If MECO's staff instead typed PH times into a UTC-configured site, these are 8 h late; the images carry each outage's own date and time either way. MCWD cards show a date with no time, so `posted_at` is empty and the date stays in the text. PAGASA prints "Issued at 08:52 AM 04 October 2026" (PH), which is exact.
- **Notices live in images.** MECO's posts have no text at all, just one image per outage (date, time, affected areas, reason, ticket number). The compiler has to read images.

## Sampling: what this set does and does not represent

- **Logged-out Facebook reaches only the top of each Page**, 1 to 4 posts and then the login wall, visible tab or not; how many load varies from run to run. MECO's and DepEDLLC's Pages show nothing at all. Facebook is therefore 11 of the 167 posts (16 captured, 5 set aside as above), all from 2026-10-01 to 10-04, mostly non-disruptions (Teachers' Day, pageants, SK sports). Class suspensions, which the city and DepEd post only on Facebook, are **absent**: the set cannot measure that type.
- **The websites carry the disruptions:** MECO and MCWD give 120 of the 125 disruption-like posts. So the set over-represents power and water, written in each utility's fixed template, and under-represents the free-form Bisaya and Filipino city posts the spec's opener is about.
- **DepEd's website is easy negatives** (olympiad and training memos). It is capped at 20 so it cannot inflate overall accuracy; score per source anyway.
- **Recency:** posts are the newest available at capture time, during an unusual month (Visayas grid red and yellow alerts, rotational brownouts). Disruption rates here are higher than in a normal month.
- **Comments are never read**; no commenter names are stored. Only the issuer's own words and images.

## Re-running

```
uv run python -m collect.sites meco mcwd deped pagasa
uv run python -m collect.facebook LLCInformationOffice metrocebuwater NGCPph DepEdTayoLapulapuCity BasakSKCouncil
uv run python -m collect.website        # city site, stale
```

Posts dedupe by `post_id`, so re-runs only add new notices. PAGASA keeps no archive, so it accumulates only if run on a schedule.
