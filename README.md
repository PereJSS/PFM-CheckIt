# CheckIt

Plataforma web para inspecciones de propiedades con evidencias fotográficas, trazabilidad criptográfica y generación de informes periciales en PDF firmados digitalmente.

---

## Resumen funcional

CheckIt separa dos flujos de trabajo:

- Administrador: crea propiedades, gestiona operarios, asigna inspecciones y descarga informes.
- Operario: ejecuta la inspección en campo, sube evidencias y completa el proceso.

Cuando una inspección se completa, el backend puede generar un informe PDF firmado con certificado X.509 y con metadatos de integridad de evidencias (hash + TSA cuando se envía).

---

## Stack tecnológico

| Capa                | Tecnología                                   |
| ------------------- | -------------------------------------------- |
| Backend             | Django 4.2, Django REST Framework, SimpleJWT |
| Frontend            | React 19, Vite, Tailwind CSS v4              |
| Persistencia        | SQLite (local), PostgreSQL (producción)      |
| Firma PDF           | ReportLab, pyHanko                           |
| Integridad temporal | RFC3161 (TSA)                                |
| Tareas asíncronas   | Celery 5.3, Redis (broker)                   |

---

## Estructura del repositorio

```text
checkIt/
├── run_gunicorn.sh         # Arranque de Django con gunicorn
├── requirements.txt        # Dependencias del entorno/host (no app)
├── checkIt/
│   ├── Procfile             # Procesos web + worker para Render
│   ├── core/                # settings, urls, wsgi, asgi, celery.py
│   ├── users/               # auth y roles (admin/operario)
│   ├── propiedades/         # CRUD de propiedades
│   ├── inspecciones/        # inspecciones, evidencias, PDF, TSA, tasks.py
│   ├── integraciones/       # API Keys B2B y webhooks de PMS externos
│   ├── certs/               # clave/certificado de firma PDF
│   ├── media/               # ficheros subidos (excluido de git)
│   ├── tmp/                 # PDFs generados/firmados (excluido de git)
│   ├── generar_certs.py     # script para crear certificado local de firma
│   ├── seed_demo.py         # script de carga de datos demo
│   ├── manage.py
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/           # login, register, admin, inspeccion
│   │   ├── components/      # formularios y UI
│   │   ├── services/        # api, offline, authStorage
│   │   └── utils/
│   └── package.json
└── README.md
```

---

## Modelo de acceso y seguridad

### Roles

- Administrador
  - Gestiona propiedades.
  - Crea y elimina operarios.
  - Crea inspecciones.
  - Descarga informes PDF de inspecciones de sus propiedades.

- Operario
  - Ve únicamente sus inspecciones asignadas.
  - Sube evidencias de inspecciones asignadas.

### Reglas importantes de autorización

- El frontend protege rutas por rol:
  - / para administrador.
  - /operario para operario.
- El endpoint `claim-report` inicia la generación del PDF (async) o lo descarga si ya está listo; solo el admin propietario de la inspección puede acceder.
- El endpoint `claim-report-status` permite sondear el estado de generación (PENDIENTE / GENERANDO / LISTO / ERROR).
- El endpoint de subida de evidencias permite al operario asignado o al admin propietario.

### Sesión por pestaña

La sesión de frontend usa sessionStorage (no localStorage):

- Cada pestaña conserva su usuario de forma independiente.
- Un login en otra pestaña no sobrescribe la sesión activa al recargar.

---

## Puesta en marcha local

### Requisitos

- Python 3.10+ (`psycopg[binary]==3.3.4` no instala en versiones anteriores)
- Node.js 18+

### Backend

```bash
# Desde la raíz del repo
python3 -m venv .venv          # o, sin Python 3.10+ en el sistema: uv venv --python 3.11 .venv
source .venv/bin/activate
pip install -r checkIt/requirements.txt

cd checkIt
python manage.py migrate
python manage.py runserver
```

Backend disponible en:

- http://localhost:8000

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend disponible en:

- http://localhost:5173

Vite proxifica automáticamente /api hacia http://localhost:8000 en desarrollo.

Nota: el script run_gunicorn.sh espera el entorno virtual en .env. Si prefieres .venv, ajusta ese script o crea el entorno con nombre .env.

## Certificado de firma (entorno local)

Para generar clave privada y certificado de firma X.509 de desarrollo:

```bash
cd checkIt
python generar_certs.py
```

Esto crea los archivos en checkIt/certs.

## Ejecución con gunicorn (producción)

Desde la raíz del repositorio y con el entorno virtual creado en .env:

```bash
chmod +x run_gunicorn.sh
./run_gunicorn.sh
```

Opcionalmente puedes indicar número de workers:

```bash
./run_gunicorn.sh 4
```

---

## Variables de entorno backend

El proyecto usa python-decouple. Variables disponibles:

| Variable              | Obligatoria | Descripción                                          |
| --------------------- | ----------- | ---------------------------------------------------- |
| `SECRET_KEY`          | Sí          | Clave secreta Django                                 |
| `DEBUG`               | No          | Por defecto `False`                                  |
| `ALLOWED_HOSTS`       | No          | Hosts permitidos                                     |
| `REDIS_URL`           | En producción | URL de Redis: broker de Celery y, si está definida, caché compartida (throttling y lock del re-sellado TSA) |
| `WEBHOOK_THROTTLE_RATE` | No        | Límite del endpoint de webhooks (por defecto `120/min`) |
| `INTEGRACIONES_THROTTLE_RATE` | No  | Límite de las vistas de gestión del panel de Integraciones (API keys, eventos, destinos, entregas) por usuario (por defecto `120/min`) |
| `AUDIT_THROTTLE_RATE` | No          | Límite de la auditoría pública por IP (por defecto `30/min`) |
| `AUTH_LOGIN_THROTTLE_RATE` | No     | Límite de login por IP (por defecto `10/min`) |
| `AUTH_USUARIO_THROTTLE_RATE` | No   | Límite de login por username, independiente de la IP (por defecto `5/min`) |
| `AUTH_REGISTER_THROTTLE_RATE` | No  | Límite de registro por IP (por defecto `5/min`) |
| `NUM_PROXIES`         | No          | Proxies de confianza delante de la app, para obtener la IP real de `X-Forwarded-For` (por defecto `1`). **En Render es `2`** (Cloudflare + balanceador de Render; verificado el 15 oct de 2026). Con un valor bajo, la identidad del throttling es la IP del proxy y los clientes comparten contador; con uno alto, se elude falsificando `X-Forwarded-For` |
| `WEBHOOK_SALIENTE_TIMEOUT_CONEXION` | No | Timeout de conexión de los webhooks salientes en segundos (por defecto `3`) |
| `WEBHOOK_SALIENTE_TIMEOUT` | No     | Timeout de lectura de los webhooks salientes en segundos (por defecto `5`) |
| `WEBHOOK_SALIENTE_MAX_REINTENTOS` | No | Reintentos ante 5xx o error de red (por defecto `5`) |
| `WEBHOOK_SALIENTE_BACKOFF_BASE` | No | Base del backoff exponencial en segundos: `base·2^n` (por defecto `30`) |
| `WEBHOOK_SALIENTE_MAX_DESTINOS` | No | Máximo de destinos activos por administrador (por defecto `10`) |
| `WEBHOOK_SALIENTE_PERMITIR_LOCAL` | No | **Solo desarrollo.** Permite `http` y destinos locales o privados, desactivando la protección SSRF (por defecto `False`). Con `DEBUG=False` el arranque avisa (`integraciones.W001`) |
| `WEBHOOK_ENTREGAS_RETENCION_DIAS` | No | Días que se conservan las entregas cerradas (entregadas o fallidas) antes de la purga diaria vía Celery beat (por defecto `90`) |
| `BACKEND_PUBLIC_URL` | No | URL pública del backend, base de los enlaces absolutos de los webhooks como `pdf_url` (por defecto `https://pfm-checkit.onrender.com`) |
| `PDF_URL_CADUCIDAD_HORAS` | No | Validez en horas del enlace firmado de descarga del PDF que lleva el webhook `pdf.listo` (por defecto `72`) |
| `TSA_URL`             | No          | Servidor de sellado RFC 3161 (por defecto FreeTSA)   |
| `TSA_TIMEOUT`         | No          | Timeout en segundos del sellado TSA, aplicado a la conexión y a cada lectura (por defecto `3`; valores no válidos o ≤ 0 vuelven a `3`). Si se supera, la evidencia se guarda sin sello |
| `TSA_RESELLO_BATCH_SIZE` | No        | Máximo de evidencias sin sello que reintenta cada ejecución de la tarea periódica (por defecto `20`) |
| `TSA_RESELLO_MAX_FALLOS` | No        | Fallos consecutivos tras los que se corta el lote (por defecto `3`) |
| `TSA_RESELLO_PAUSA_SEG` | No        | Pausa en segundos entre peticiones a la TSA durante el re-sellado (por defecto `1`) |
| `TSA_RESELLO_INTERVALO_MIN` | No     | Minutos entre ejecuciones del re-sellado vía Celery beat (por defecto `10`) |
| `DB_HOST`             | Producción  | Host PostgreSQL                                      |
| `DB_NAME`             | Producción  | Nombre de la base de datos                           |
| `DB_USER`             | Producción  | Usuario PostgreSQL                                   |
| `DB_PASSWORD`         | Producción  | Contraseña PostgreSQL                                |
| `DB_PORT`             | No          | Puerto PostgreSQL (por defecto `5432`)               |
| `CORS_ALLOWED_ORIGINS`| No          | Orígenes permitidos para CORS                        |
| `CSRF_TRUSTED_ORIGINS`| No          | Orígenes de confianza para CSRF                      |

Ejemplo mínimo de .env para desarrollo local (sin Redis):

```env
SECRET_KEY=tu_clave_local_segura
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

Sin `REDIS_URL`, el broker apunta a `redis://localhost:6379/0`. La API funciona con normalidad pero el endpoint `claim-report` requiere Redis y un worker Celery corriendo para generar el PDF.

---

## Datos de demo

Desde checkIt/ con entorno virtual activo:

```bash
python manage.py shell < seed_demo.py
```

Usuarios demo:

| Usuario       | Contraseña   | Rol           |
| ------------- | ------------ | ------------- |
| admin_demo    | checkIt2026! | Administrador |
| admin_partner | checkIt2026! | Administrador |
| operario1     | checkIt2026! | Operario      |
| operario2     | checkIt2026! | Operario      |

El seed actual crea 6 propiedades y 6 inspecciones de ejemplo, repartidas entre ambos administradores.

---

## API principal

Base URL: /api/v1

### Autenticación

| Método | Ruta            | Descripción                  | Auth    |
| ------ | --------------- | ---------------------------- | ------- |
| POST   | /auth/register/ | Registro de administrador    | Pública |
| POST   | /auth/login/    | Obtiene access y refresh JWT | Pública |
| POST   | /auth/refresh/  | Renueva access token         | Pública |
| GET    | /auth/me/       | Usuario autenticado actual   | JWT     |

### Operarios

| Método | Ruta                      | Descripción      | Auth        |
| ------ | ------------------------- | ---------------- | ----------- |
| GET    | /usuarios/operarios/      | Lista operarios  | JWT (admin) |
| POST   | /usuarios/operarios/      | Crea operario    | JWT (admin) |
| DELETE | /usuarios/operarios/{id}/ | Elimina operario | JWT (admin) |

### Propiedades

| Método    | Ruta               | Descripción                 | Auth        |
| --------- | ------------------ | --------------------------- | ----------- |
| GET       | /propiedades/      | Lista propiedades del admin | JWT         |
| POST      | /propiedades/      | Crea propiedad              | JWT         |
| GET       | /propiedades/{id}/ | Detalle propiedad           | JWT (owner) |
| PUT/PATCH | /propiedades/{id}/ | Actualiza propiedad         | JWT (owner) |
| DELETE    | /propiedades/{id}/ | Elimina propiedad           | JWT (owner) |

Cada propiedad admite un `external_id` opcional (único por admin): el identificador de esa propiedad en el PMS del cliente, usado para enlazar los webhooks entrantes.

### Inspecciones y evidencias

| Método    | Ruta                             | Descripción                          | Auth                                        |
| --------- | -------------------------------- | ------------------------------------ | ------------------------------------------- |
| GET       | /inspecciones/                   | Lista inspecciones filtradas por rol | JWT                                         |
| POST      | /inspecciones/                   | Crea inspección                      | JWT                                         |
| GET       | /inspecciones/{id}/              | Detalle inspección                   | JWT con alcance por rol                     |
| PUT/PATCH | /inspecciones/{id}/              | Actualiza inspección                 | JWT con alcance por rol                     |
| DELETE    | /inspecciones/{id}/              | Elimina inspección                   | JWT con alcance por rol                     |
| GET       | /inspecciones/{id}/claim-report/        | Inicia generación de PDF (202) o descarga si ya está listo (200) | JWT (admin propietario) |
| GET       | /inspecciones/{id}/claim-report-status/ | Estado del PDF: PENDIENTE / GENERANDO / LISTO / ERROR            | JWT (admin propietario) |
| POST      | /inspecciones/{id}/evidencias/          | Sube evidencia con hash/TSA opcional                             | JWT (operario asignado o admin propietario) |

### Auditoría pública

| Método | Ruta               | Descripción                            | Auth    |
| ------ | ------------------ | -------------------------------------- | ------- |
| GET    | /audit/{id}/       | Consulta hash y timestamp de evidencia | Pública |
| GET    | /audit/{id}/token/ | Descarga token TSA en binario          | Pública |

### Integraciones (PMS externos)

| Método | Ruta                             | Descripción                                             | Auth            |
| ------ | -------------------------------- | ------------------------------------------------------- | --------------- |
| GET    | /integraciones/api-keys/         | Lista las API Keys del admin (sin la clave)             | JWT (admin)     |
| POST   | /integraciones/api-keys/         | Crea API Key `{"nombre": "..."}`; la clave solo se muestra en esta respuesta | JWT (admin) |
| DELETE | /integraciones/api-keys/{id}/    | Revoca la API Key (404 si es de otro admin)             | JWT (admin)     |
| GET    | /integraciones/eventos/          | Últimos 50 eventos recibidos y su estado                | JWT (admin)     |
| POST   | /webhooks/pms/                   | Recibe un evento del PMS                                | API Key         |
| GET/POST | /integraciones/destinos/       | Lista o crea destinos de webhooks salientes; el secreto solo se muestra al crear | JWT (admin) |
| GET/PATCH/DELETE | /integraciones/destinos/{id}/ | Detalle, edición (`activo`, `eventos`…) o borrado del destino | JWT (admin) |
| POST   | /integraciones/destinos/{id}/rotar-secreto/ | Genera un secreto nuevo (el anterior deja de valer) y lo muestra una vez | JWT (admin) |
| GET    | /integraciones/entregas/         | Últimas 50 entregas; filtros `?destino=` y `?estado=`   | JWT (admin)     |
| POST   | /integraciones/entregas/{id}/reintentar/ | Reencola una entrega `FALLIDA` o `PENDIENTE` atascada (>10 min); 409 en otro caso | JWT (admin) |

---

## Webhooks de PMS (API Keys)

CheckIt puede recibir eventos de un PMS/ERP externo (Guesty, Hostaway, Smoobu…) sin que el cliente cambie de software. Flujo: **reserva finalizada → inspección pendiente automática**.

1. El admin crea una API Key en `POST /api/v1/integraciones/api-keys/` y la configura en su PMS. Solo se guarda su hash SHA-256: si se pierde, se revoca y se crea otra.
2. El admin rellena el `external_id` de cada propiedad con el ID que usa su PMS.
3. El PMS envía los eventos:

```http
POST /api/v1/webhooks/pms/
Authorization: Api-Key ck_<prefijo>_<secreto>
Content-Type: application/json

{
  "event_id": "evt_123",
  "event_type": "reservation.checkout",
  "provider": "guesty",
  "data": { "property_id": "LISTING-42", "reservation_id": "R-998" }
}
```

| Respuesta | Significado |
| --------- | ----------- |
| `202`     | Evento nuevo guardado y encolado en Celery |
| `202` con `"duplicado": true` | Reenvío de un evento que quedó en `RECIBIDO` o `ERROR`: se vuelve a encolar (p. ej. tras enlazar el `external_id` que faltaba) |
| `200` con `"duplicado": true` | Reenvío de un evento ya `PROCESADO` o `IGNORADO`; no se repite |
| `400`     | Payload inválido (falta `event_id` o `event_type`, o `data.property_id` no es texto/número en un checkout) |
| `401`     | API Key ausente, incorrecta o revocada |
| `429`     | Límite de peticiones superado (`WEBHOOK_THROTTLE_RATE`, por defecto `120/min`) |

Seguridad:

- La API Key **solo** sirve en `/webhooks/pms/`; no da acceso al resto de la API (que sigue siendo JWT). Una clave filtrada no permite leer evidencias ni gestionar operarios.
- El evento solo puede crear inspecciones en propiedades del admin dueño de la clave.
- Idempotencia por `(admin, provider, event_id)` + bloqueo de fila al procesar: los reintentos nunca duplican inspecciones.
- `data.property_id` se normaliza (texto, sin espacios) igual que el `external_id` de la propiedad.

Procesamiento (`integraciones/tasks.py`): `reservation.checkout` crea una inspección `PENDIENTE` sin operario, que el admin asigna desde el panel. Si la propiedad no existe el evento queda en `ERROR` (visible en `/integraciones/eventos/`); otros tipos de evento quedan en `IGNORADO`. Si el broker Redis no responde (timeout de 2 s), el evento se procesa en línea para no perderlo. Si el procesamiento falla en línea o tras los 3 reintentos de Celery, el evento queda en `ERROR` con el motivo, y el PMS puede reenviarlo.

---

## Webhooks salientes (CheckIt → PMS)

Cada admin puede registrar destinos (`/integraciones/destinos/`, o desde la pestaña **Integraciones** del panel) para que CheckIt avise a su PMS:

- `inspeccion.completada`: la inspección pasa a `COMPLETADA`.
- `pdf.listo`: el PDF firmado pasa a `LISTO`.

Los eventos se emiten solo en la transición (señales sobre `Inspeccion`) y se encolan tras el commit. Payload:

```json
{"event_id": "…", "tipo": "pdf.listo", "fecha": "…",
 "data": {"inspeccion_id": 1, "propiedad_id": 2, "external_id": "LISTING-42", "estado": "COMPLETADA", "pdf_estado": "LISTO"}}
```

Cabeceras: `X-CheckIt-Signature: sha256=<hex>` (HMAC-SHA256 del cuerpo crudo con el secreto `whsec_…`), `X-CheckIt-Event` y `X-CheckIt-Delivery` (estable entre reintentos). El receptor debe verificar la firma con comparación en tiempo constante, rechazar `fecha` antiguas y **deduplicar por `X-CheckIt-Delivery`**: la entrega es *at-least-once*.

- **Reintentos**: ante 5xx o error de red, backoff exponencial (`30·2^n` s, hasta 5). Un 4xx o 3xx deja la entrega `FALLIDA` sin reintentar; no se siguen redirecciones.
- **Recuperación**: Celery beat ejecuta cada 5 min `reencolar_entregas_atascadas` (entregas `PENDIENTE` sin actividad >10 min, p. ej. con el broker caído). También se pueden reintentar a mano.
- **Protección SSRF**: solo `https`; se rechazan userinfo, `\`, URLs ambiguas y hosts que resuelvan a IPs privadas, loopback, link-local, metadatos de nube o rangos de transición IPv6. La IP se valida en el momento de conectar y la conexión se fija a esa IP (con SNI y certificado del hostname original), sin proxies del entorno. Depende de `requests==2.32.4` y `urllib3==2.2.3`: revisar `integraciones/salientes.py` antes de actualizarlas.
- **Pendiente**: la URL del PDF en `pdf.listo` (requiere un endpoint de descarga firmado) y la purga de entregas antiguas.

---

## Documentación de la API (OpenAPI) y límites de peticiones

La documentación es pública porque la usan los integradores de PMS:

- Esquema: `/api/v1/schema/`
- Swagger: `/api/v1/docs/`. Para probar el webhook, pulsa **Authorize** en `ApiKeyAuth` y escribe `Api-Key ck_<prefijo>_<secreto>`.
- Redoc: `/api/v1/redoc/`

El esquema se valida con `python manage.py spectacular --validate --fail-on-warn` (también en el CI).

Límites (respuesta `429` con `Retry-After`): auditoría pública, login (por IP **y** por username), registro y webhooks de entrada; ver las variables `*_THROTTLE_RATE`. La IP del cliente se toma de `X-Forwarded-For` según `NUM_PROXIES`. Los contadores viven en la caché: en producción define `REDIS_URL` para que sean compartidos entre procesos.

---

## Generación de PDF asíncrona (Celery + Redis)

El endpoint `claim-report` ya no bloquea el worker HTTP durante la firma X.509. El flujo es:

1. El admin llama a `GET /inspecciones/{id}/claim-report/`.
2. Si el PDF ya está generado (`pdf_estado=LISTO`), se descarga directamente.
3. Si no, el backend encola la tarea Celery y devuelve **202 Accepted** con un `task_id`.
4. El frontend sondea `GET /inspecciones/{id}/claim-report-status/` hasta que `pdf_listo=true`.
5. Una segunda llamada a `claim-report/` descarga el fichero.

El worker reintenta la generación hasta 3 veces (intervalo 60 s) si falla. Tras 5 minutos sin completarse, la tarea expira y `pdf_estado` pasa a `ERROR`.

### Arranque local con Redis

```bash
# Terminal 1 — Redis (requiere Redis instalado)
redis-server

# Terminal 2 — Worker Celery
source .env/bin/activate
cd checkIt
celery -A core worker --loglevel=info
```

### Re-sellado TSA periódico (Celery beat)

Si FreeTSA falla o supera el timeout, la evidencia se guarda sin sello (modo degradado). La tarea `inspecciones.tasks.resellar_evidencias_pendientes` se ejecuta cada 10 minutos (`CELERY_BEAT_SCHEDULE`) y reintenta el sellado de las evidencias con hash y sin `tsa_token`, en lotes de `TSA_RESELLO_BATCH_SIZE`, con pausa entre peticiones y corte tras varios fallos consecutivos (para no martillear a FreeTSA si está caído). Un fallo individual se registra en el log y no interrumpe el lote. Un lock en caché evita ejecuciones solapadas. Requiere un planificador beat; la opción recomendada es embeberlo en el worker existente (una sola instancia):

```bash
celery -A core worker -B --loglevel=info
```

Alternativa con proceso separado:

```bash
celery -A core beat --loglevel=info
```

El `Procfile` incluye el proceso `beat` como alternativa documentada. Render **no** lee el `Procfile`: allí el comando se define en el Start command de cada servicio.

### Despliegue en Render

Configuración actual de producción (servicio web con *Root Directory* `checkIt`):

| Ajuste | Valor |
|---|---|
| Pre-Deploy Command | incluye `python manage.py migrate` |
| Start Command | `celery -A core worker -B --pool=solo --loglevel=info & gunicorn core.wsgi` |
| `PYTHON_VERSION` | `3.11.9` (también fijado en `checkIt/.python-version`; la variable tiene prioridad) |
| `REDIS_URL` | *Internal URL* de un **Key Value** de Render (plan gratuito, misma región que el web) |
| `NUM_PROXIES` | `2` |

Celery (con beat embebido, `-B`) corre **dentro del servicio web** porque los Background Workers de Render no tienen plan gratuito. `--pool=solo` usa un único proceso para no agotar los 512 MB. Limitaciones: comparte memoria con gunicorn, se duerme con el servicio web en el plan gratuito y, si Celery se cae, nadie lo reinicia hasta el siguiente deploy. Con clientes reales, mover Celery a un **Background Worker** (mismo Build Command y variables, Start Command `celery -A core worker -B --loglevel=info`, **una sola instancia** para no duplicar beat) y dejar el web en `gunicorn core.wsgi`.

---

## Experiencia de uso en panel administrador

La tabla de inspecciones se actualiza sin recargar toda la página:

- Polling automático cada ~15 segundos.
- Refresco al volver el foco a la pestaña.
- Refresco cuando la pestaña pasa a estado visible.

Esto permite ver cambios de estado y disponibilidad del botón de descarga de PDF en tiempo casi real.

---

## Flujo funcional resumido

```text
Admin registra cuenta
  -> crea propiedades
    -> crea operarios
      -> crea inspecciones
        -> operario sube evidencias (hash SHA-256 + sello TSA)
          -> completa inspección
            -> admin solicita informe (claim-report → 202)
              -> worker Celery genera y firma PDF (X.509)
                -> admin sondea estado (claim-report-status)
                  -> descarga informe cuando pdf_estado=LISTO
```

---

## CI

GitHub Actions (`.github/workflows/ci.yml`) se ejecuta en cada push y pull request a `master`, sin secretos:

- **Backend**: Python 3.10 y 3.13 (3.10 es el mínimo por `psycopg[binary]==3.3.4`), instala `checkIt/requirements.txt`, ejecuta `makemigrations --check --dry-run` y `manage.py test` (SQLite; Celery y TSA mockeados).
- **Backend** también valida el esquema OpenAPI (`spectacular --validate --fail-on-warn`).
- **Frontend**: Node 20, `npm ci`, `npm run lint -- --max-warnings 0` y `npm run build`. El lockfile se regenera con `npx npm@10.8.2 install` para que siga siendo compatible con el npm de Node 20.
