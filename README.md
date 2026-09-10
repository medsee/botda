# memebot v0.1 — PAPER TRADING

To'liq avtomatik memecoin sistemasi. **Lekin hozircha faqat qog'ozda (paper).**
Haqiqiy pul kiritilmaydi, hech qanday tranzaksiya imzolanmaydi.

---

## 1. Nima uchun avval paper

Siz "pul zarar qilmasin" dedingiz. Rostini aytaman: **bunday kafolat bo'lmaydi.**
Lekin quyidagilar kafolatlanadi, chunki ular kodda yozilgan:

- har bir savdoda faqat kapitalning **1.5%** risk qilinadi
- har bir pozitsiyada **−20% stop-loss** bor
- kunlik zarar **3%** ga yetganda savdo to'xtaydi
- bir vaqtda ko'pi bilan **5 ta** pozitsiya, kapitalning **25%** dan ortig'i band bo'lmaydi
- foyda bosqichma-bosqich chiqariladi (+50% da 40%, +100% da 30%, +200% da 20%)

Bu qoidalar "foyda" kafolatlamaydi. Ular **halokatni** oldini oladi.

---

## 2. Ishga tushirish

```bash
cd "C:/Users/VICTUS/WorkBuddy AI/2026-09-10-09-48-14/memebot"

# Python (managed):
PY="C:/Users/VICTUS/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"

$PY engine.py --scan-only      # faqat skan, hech narsa olmaydi
$PY engine.py --once           # bitta to'liq sikl
$PY engine.py                  # uzluksiz ishlaydi (har 60 soniyada)
$PY engine.py --report         # shu vaqtgacha natija
$PY simulate.py --trades 2000  # Monte-Karlo: qoidalar pul qiladimi?
```

Hech qanday API kalit kerak emas — ma'lumot DexScreener bepul API'dan olinadi.
Hech qanday kutubxona o'rnatish kerak emas — faqat standart Python.

---

## 3. Modullar

| Fayl | Vazifasi |
|---|---|
| `scanner.py` | Yangi tokenlarni topadi, filtrlaydi, 0–100 ball beradi |
| `risk.py` | Pozitsiya hajmi, portfel limitlari, chiqish qoidalari, xarajat modeli |
| `store.py` | SQLite da to'liq tarix (hech narsa o'chmaydi) |
| `engine.py` | Asosiy sikl: avval chiqishlar, keyin kirishlar |
| `simulate.py` | Minglab sintez-savdolar orqali qoidalarni sinaydi |
| `config.json` | Barcha sozlamalar — faqat shu yerda o'zgartiring |

---

## 4. Filtrlar (config.json → filters)

| Filtr | Qiymat | Nima uchun |
|---|---|---|
| `min_liquidity_usd` | 8 000 | Likvidlik yo'q joyda chiqib bo'lmaydi |
| `min_volume_h1_usd` | 3 000 | O'lik token |
| `min_txns_m5` | 10 | Faollik yo'q |
| `min_buy_ratio_m5` | 0.9 | Sotuvchilar ustun bo'lsa — kirmaymiz |
| `min_age_minutes` | 3 | Juda yangi — snayperlar zonasi |
| `max_age_minutes` | 2880 | 2 kundan keyin memecoin o'ladi |
| `max_change_m5_pct` | 180 | Allaqachon pump bo'lgan — kechikkanmiz |
| `min/max_fdv_usd` | 15k / 3M | O'sish uchun joy bo'lishi kerak |

Ball `scoring.min_score_to_trade` (hozirda 55) dan past bo'lsa — savdo yo'q.

---

## 5. Chiqish qoidalari (tartib muhim)

1. **Hard stop −20%** — har doim to'liq chiqish
2. **Vaqt stop 240 min** — memecoinga uylanib qolmaslik
3. **Stale 30 min** — harakat bo'lmasa, slotni bo'shatish
4. **Trailing stop 15%** — +25% dan keyin ishga tushadi, cho'qqidan 15% pastga
5. **TP zinapoyasi** — +50/+100/+200 da qisman sotish

---

## 6. Xarajat modeli (ko'pchilik bu yerda o'ladi)

```
har bir tomonga 110 bps komissiya (1.1%)
+ $0.30 priority fee (Solana)
+ 2% kirish slippage, 3% chiqish slippage
```

Ya'ni bitta "aylanma" (kirish + chiqish) taxminan **7–8%** turadi.
Token 8% o'smasa, siz allaqachon zarardasiz. Shuning uchun filtrlar
juda qattiq bo'lishi kerak.

---

## 7. Bugungi sinov natijasi (haqiqiy ma'lumot, 2026-09-10)

```
scanned 28 tokens, 0 passed filters
top rejections: sellers dominating x27, liquidity too low x15, ...
```

Bot bugun **hech narsa sotib olmadi**. Bu xato emas — bu to'g'ri xatti-harakat.
Bozorda hozir sotuvchilar ustun, memecoin ulushi tarixiy minimumda (~2.8%).

---

## 8. Monte-Karlo natijasi (`simulate.py --trades 2000`)

| Filtr sifati (edge) | Win% | O'rtacha/savdo | 500$ dan keyin | Bust |
|---|---|---|---|---|
| 0% (tasodifiy kirish) | 25.3% | −$1.96 | $0 | 257 savdoda |
| 20% | 24.4% | −$3.82 | $0 | 131 savdoda |
| 30% | 23.3% | −$0.38 | $0 | 1389 savdoda |
| 50% | 24.2% | +$0.18 | $852 | yo'q |
| 70% | 27.9% | +$1.95 | $4 393 | yo'q |

**Xulosa: zararsizlik chegarasi taxminan 50% filtr sifatini talab qiladi.**
Ya'ni filtrlaringiz axlat tokenlarning yarmini to'sishi kerak. Bu juda qiyin.

Bu sintez-model, bashorat emas. Uning vazifasi — qoidalarni bir-biri bilan
solishtirish, foyda va'da qilish emas.

---

## 9. Nega `live` rejimi yo'q

`config.json` da `mode: "paper"`. `live` qo'ysangiz bot to'xtaydi va
xatolik chiqaradi. Buning sababi: haqiqiy pul ulashdan oldin
**kamida 100 ta yopiq paper-savdo** kerak, va ularning natijasi
xarajatlardan keyin ham musbat bo'lishi shart.

Haqiqiy savdo qo'shish uchun kerak bo'ladi:
- Helius yoki QuickNode RPC (bepul emas, lekin arzon)
- Hamyon + xususiy kalit (faqat alohida, kichik "jangovar" hamyon)
- Jupiter / Raydium execution moduli
- Token xavfsizlik tekshiruvi: mint authority, freeze authority, LP locked

---

## 10. Keyingi qadamlar (muhimlik tartibida)

1. **100 ta paper-savdo to'plang** — `python engine.py` ni bir necha kun qoldiring
2. `--report` bilan win rate va exit sabablarini ko'ring
3. Filtrlarni faqat natijaga qarab sozlang, his-tuyg'uga qarab emas
4. Keyin holderlar tahlili (RPC kerak) — eng katta edge shu yerda
5. Faqat shundan keyin live execution

---

## Ogohlantirish

Memecoin bozori — nol yig'indili o'yin: kimdir foyda ko'rsa, boshqasi
yo'qotadi. 2026-yil ma'lumotlari bo'yicha Solana memecoin treyderlarining
atigi 6.25% i 90 kun ichida foyda bilan chiqqan, median treyder −120$.
Bu bot sizni statistikadan ustun qilmaydi — faqat intizomli qiladi.
