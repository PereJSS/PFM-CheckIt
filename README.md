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

---

## Estructura del repositorio

```text
checkIt/
├── run_gunicorn.sh         # Arranque de Django con gunicorn
├── requirements.txt        # Dependencias del entorno/host (no app)
├── checkIt/
│   ├── core/                # settings, urls, wsgi, asgi
│   ├── users/               # auth y roles (admin/operario)
│   ├── propiedades/         # CRUD de propiedades
│   ├── inspecciones/        # inspecciones, evidencias, PDF, TSA
│   ├── certs/               # clave/certificado de firma PDF
│   ├── media/               # ficheros subidos
│   ├── tmp/                 # PDFs generados/firmados
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
- El endpoint claim-report permite descargar PDF solo al admin propietario de la inspección.
- El endpoint de subida de evidencias permite al operario asignado o al admin propietario.

### Sesión por pestaña

La sesión de frontend usa sessionStorage (no localStorage):

- Cada pestaña conserva su usuario de forma independiente.
- Un login en otra pestaña no sobrescribe la sesión activa al recargar.

---

## Puesta en marcha local

### Requisitos

- Python 3.8+
- Node.js 18+

### Backend

```bash
# Desde la raíz del repo
python3 -m venv .env
source .env/bin/activate
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

El proyecto usa python-decouple. Debes definir al menos:

- SECRET_KEY (obligatoria)
- DEBUG (opcional, por defecto false)
- ALLOWED_HOSTS (opcional)

Y para despliegue frontend/backend separados también:

- CORS_ALLOWED_ORIGINS
- CSRF_TRUSTED_ORIGINS

Ejemplo mínimo de .env para desarrollo:

```env
SECRET_KEY=tu_clave_local_segura
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

Para PostgreSQL en producción se leen también variables DB_HOST, DB_NAME, DB_USER, DB_PASSWORD y DB_PORT.

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

### Inspecciones y evidencias

| Método    | Ruta                             | Descripción                          | Auth                                        |
| --------- | -------------------------------- | ------------------------------------ | ------------------------------------------- |
| GET       | /inspecciones/                   | Lista inspecciones filtradas por rol | JWT                                         |
| POST      | /inspecciones/                   | Crea inspección                      | JWT                                         |
| GET       | /inspecciones/{id}/              | Detalle inspección                   | JWT con alcance por rol                     |
| PUT/PATCH | /inspecciones/{id}/              | Actualiza inspección                 | JWT con alcance por rol                     |
| DELETE    | /inspecciones/{id}/              | Elimina inspección                   | JWT con alcance por rol                     |
| GET       | /inspecciones/{id}/claim-report/ | Descarga PDF firmado                 | JWT (admin propietario)                     |
| POST      | /inspecciones/{id}/evidencias/   | Sube evidencia con hash/TSA opcional | JWT (operario asignado o admin propietario) |

### Auditoría pública

| Método | Ruta               | Descripción                            | Auth    |
| ------ | ------------------ | -------------------------------------- | ------- |
| GET    | /audit/{id}/       | Consulta hash y timestamp de evidencia | Pública |
| GET    | /audit/{id}/token/ | Descarga token TSA en binario          | Pública |

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
        -> operario sube evidencias
          -> completa inspección
            -> admin descarga informe firmado
```
