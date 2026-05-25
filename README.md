# CheckIt

Plataforma web de inspecciones de propiedades con generación de informes periciales certificados digitalmente.

## ¿Qué hace?

Un administrador (propietario / gestor) crea su cuenta, registra sus inmuebles y asigna inspecciones a operarios. El operario accede desde su móvil, fotografía los daños y añade descripciones. Al completar la inspección, el sistema genera automáticamente un **informe PDF firmado con certificado X.509** y sellado con un **timestamp TSA** que garantiza su validez legal.

---

## Stack

| Capa          | Tecnología                                     |
| ------------- | ---------------------------------------------- |
| Backend       | Django 4.2 · Django REST Framework · SimpleJWT |
| Frontend      | React 19 · Vite · Tailwind CSS v4              |
| Base de datos | SQLite (dev)                                   |
| PDF           | ReportLab · pyHanko (firma X.509)              |
| Timestamp     | TSA (RFC 3161)                                 |

---

## Estructura del proyecto

```
checkIt/
├── checkIt/               # Proyecto Django
│   ├── core/              # Configuración (settings, urls)
│   ├── users/             # Modelo de usuario (Admin / Operario)
│   ├── propiedades/       # Gestión de inmuebles
│   ├── inspecciones/      # Inspecciones, evidencias y generación de PDF
│   │   └── services/      # pdf_generator, pdf_signer, tsa
│   └── manage.py
└── frontend/              # App React (Vite)
    └── src/
        ├── pages/         # login, register, admin, inspeccion
        ├── components/    # inspectionForm
        └── services/      # api.js (axios + JWT interceptors)
```

---

## Roles

| Rol               | Acceso                                                                                                      |
| ----------------- | ----------------------------------------------------------------------------------------------------------- |
| **Administrador** | Registro propio · Gestión de propiedades · Creación de operarios · Panel de inspecciones + descarga de PDFs |
| **Operario**      | Lista de sus inspecciones asignadas · Formulario de evidencias fotográficas                                 |

---

## Puesta en marcha (desarrollo)

### Requisitos

- Python 3.8+
- Node.js 18+

### Backend

```bash
# Crear y activar entorno virtual
python3 -m venv .env
source .env/bin/activate

# Instalar dependencias
pip install -r checkIt/requirements.txt

# Aplicar migraciones
cd checkIt
python manage.py migrate

# Iniciar servidor (puerto 8000)
python manage.py runserver
```

### Frontend

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

> Vite redirige `/api/*` a `http://localhost:8000` automáticamente.

---

## Datos de demo

Ejecutar desde `checkIt/` con el entorno activado:

```bash
python manage.py shell < seed_demo.py
```

Esto crea **5 propiedades** y **6 inspecciones** asignadas a los operarios de demo.

| Usuario      | Contraseña     | Rol           |
| ------------ | -------------- | ------------- |
| `admin_demo` | `checkIt2026!` | Administrador |
| `operario1`  | `checkIt2026!` | Operario      |
| `operario2`  | `checkIt2026!` | Operario      |

---

## API — Endpoints principales

| Método   | Ruta                                      | Descripción                       | Auth        |
| -------- | ----------------------------------------- | --------------------------------- | ----------- |
| POST     | `/api/v1/auth/register/`                  | Registro de administrador         | Pública     |
| POST     | `/api/v1/auth/login/`                     | Obtener tokens JWT                | Pública     |
| GET      | `/api/v1/auth/me/`                        | Datos del usuario actual          | JWT         |
| GET/POST | `/api/v1/propiedades/`                    | Listar / crear propiedades        | JWT (admin) |
| GET/POST | `/api/v1/usuarios/operarios/`             | Listar / crear operarios          | JWT (admin) |
| DELETE   | `/api/v1/usuarios/operarios/<id>/`        | Eliminar operario                 | JWT (admin) |
| GET/POST | `/api/v1/inspecciones/`                   | Inspecciones (filtradas por rol)  | JWT         |
| GET      | `/api/v1/inspecciones/<id>/claim-report/` | Descargar PDF firmado             | JWT (admin) |
| GET      | `/api/v1/audit/<id>/`                     | Verificar integridad de evidencia | Pública     |

---

## Flujo principal

```
Admin registra cuenta
  └─► Añade propiedades
        └─► Crea operarios
              └─► Asigna inspecciones
                    └─► Operario fotografía daños
                          └─► Sistema genera PDF firmado + TSA
                                └─► Admin descarga informe pericial
```
