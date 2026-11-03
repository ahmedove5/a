# Failed Order Alerts / تنبيهات الطلبات الفاشلة

خدمة صغيرة ترسل تنبيه (Telegram / Email / Slack / Teams) لما يصير **Failed Order**.

## تكتشف حالتين
1. **فشل صريح** — المنصة أو نظامك يرسل حالة `failed` → تنبيه فوري.
2. **فشل صامت** — الطلب وصل (`received`) بس الفرع/الـPOS ما أكّده خلال `ACK_TIMEOUT_MINUTES` → تنبيه.
   هذي أهم حالة، لأن كثير من الطلبات اللي "ما توصل للفرع" ما يجي لها أي event فشل أصلاً.

كل طلب ينبّه **مرة وحدة فقط** (ما في سبام).

## التشغيل
```bash
cp .env.example .env      # عبّي القيم
pip install -r requirements.txt
set -a; . ./.env; set +a
uvicorn app.main:app --host 0.0.0.0 --port 8000
curl -X POST localhost:8000/test-alert -H "X-Alert-Token: change-me"   # جرّب القنوات
```
أو Docker:
```bash
docker build -t order-alerts . && docker run -d --env-file .env -p 8000:8000 -v alerts:/data order-alerts
```

## طرق الربط
| Endpoint | لمين |
|---|---|
| `POST /events` | نظام الـintegration حقك (الأفضل) |
| `POST /webhooks/deliverect` | Deliverect webhooks |
| `POST /webhooks/urbanpiper` | UrbanPiper webhooks |
| `GET /alerts` | آخر التنبيهات |
| `GET /health` | القنوات المفعّلة |

### من نظامك (الأنظف)
```bash
curl -X POST https://HOST/events -H "X-Alert-Token: change-me" -H "Content-Type: application/json" \
  -d '{"order_id":"123","kind":"received","channel":"talabat","store":"Riyadh-1"}'
# لما الـPOS يأكد:
  -d '{"order_id":"123","kind":"acknowledged"}'
# لما يفشل:
  -d '{"order_id":"123","kind":"failed","reason":"POS timeout"}'
```
`kind`: `received | acknowledged | failed | cancelled`

### Telegram
1. افتح @BotFather → `/newbot` → خذ التوكن.
2. ضيف البوت للقروب وأرسل أي رسالة، بعدين افتح `https://api.telegram.org/bot<TOKEN>/getUpdates` وخذ `chat.id`.

## ⚠️ قبل الإنتاج
- أكواد حالات Deliverect و UrbanPiper في `.env.example` مبنية على التوثيق العام — **تأكد منها على payload حقيقي من حسابك** وعدّلها من الـenv بدون تعديل كود.
- خلّ الخدمة على HTTPS وحط `INGEST_TOKEN`.

## Tests
```bash
pip install pytest && python -m pytest -q
```
