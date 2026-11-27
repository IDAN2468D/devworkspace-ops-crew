# DevWorkspace Ops Crew — Microservices Architecture

מערכת `Multi-Agent` מבוזרת (`Microservices`) מבוססת `FastAPI`, `Agno`, `MCP`, `ChromaDB RAG`, `Apache Kafka` + `Kafka UI`, `Elasticsearch`, ו-`PostgreSQL` לאוטומציה יומיומית של `Gmail`, `Google Drive` ו-`GitHub` עם שער אישורים אנושי (`HITL`).

## ארכיטקטורת השירותים

| שירות | פורט | תפקיד | אחסון / תשתית |
| :--- | :--- | :--- | :--- |
| `api-gateway` | `8000` | נקודת כניסה אחידה, אבטחת `JWT` + `HttpOnly Cookie`, וניתוב לשירותים | Stateless |
| `auth-service` | `8001` | `Google OAuth 2.0`, הנפקת `JWT`, ושמירת טוקני `MCP` | `PostgreSQL` (`auth_db`) + `Kafka` |
| `hitl-service` | `8002` | תור אישורים אנושי (`HITL`) והרצת `Write Handlers` רק לאחר אישור | `PostgreSQL` (`hitl_db`) + `Kafka` |
| `rag-service` | `8003` | אינדוקס וחיפוש סמנטי במסמכי `SPEC` | `ChromaDB` + `Kafka` |
| `ops-agent-service` | `8004` | צוות סוכני `Agno` + `Gemini` וחיבורי `MCP` בקריאה בלבד | `HTTP` + `Kafka` |
| `search-service` | `8005` | צרכן `Kafka` בזמן אמת המאונדקס ל-`Elasticsearch` ומספק חיפוש ודוחות | `Elasticsearch 8` + `Kafka` |
| `kafka-ui` | `8080` | ממשק ניהול ויזואלי לצפייה ב-Topics, הודעות ו-Consumer Groups | `Apache Kafka (KRaft)` |
| `elasticsearch` | `9200` | מנוע חיפוש טקסט מלא וסינון בזמן אמת (`ops-specs`, `ops-events`) | `Elasticsearch 8.15` |

## אירועי Kafka (`Topics`)

- `auth.user.authenticated` — מפורסם ע"י `auth-service` בהתחברות/הרשמה.
- `hitl.action.requested` — מפורסם ע"י `hitl-service` בעת הפקדת פעולה לאישור.
- `hitl.action.decided` — מפורסם ע"י `hitl-service` לאחר אישור/ביצוע/דחייה.
- `rag.document.ingested` — מפורסם ע"י `rag-service` ומאונדקס בזמן אמת ע"י `search-service` ל-`Elasticsearch`.
- `ops.run.completed` — מפורסם ע"י `ops-agent-service` בסיום הרצת `OpsSupervisor`.

## הרצה מהירה עם Docker Compose

1. העתק את קובץ המשתנים והכנס את ה-`GOOGLE_API_KEY`:
   ```bash
   cp .env.example .env
   ```
2. הרם את כל המיקרוסרוויסים, `PostgreSQL`, `Kafka`, `Kafka UI` ו-`Elasticsearch`:
   ```bash
   docker compose up --build
   ```
3. ממשקים זמינים:
   * **API Gateway Swagger**: `http://127.0.0.1:8000/docs`
   * **Aggregated Health**: `http://127.0.0.1:8000/health`
   * **Kafka UI**: `http://127.0.0.1:8080`
   * **Elasticsearch**: `http://127.0.0.1:9200`
   * **חיפוש טקסט מלא ב-Elasticsearch**: `GET /api/v1/search/specs?q=...` ו-`GET /api/v1/search/events?q=...`
   * **דוח סיכום אירועים**: `GET /api/v1/search/reports/summary`

## הרצת טסטים (`pytest`)

הטסטים רצים בצורה מבודדת (ללא תלות בשרתי `Kafka` / `Elasticsearch` / `Gemini` חיצוניים):

```bash
pytest -v
```
