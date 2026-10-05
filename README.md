# NEX-CONTROL

Dashboard web de administración (FastAPI + Jinja2 + SQLite) para el
control de vehículos, usuarios y auditoría del proyecto NEXORA.

## Requisitos

- Python 3.10 o superior.
- Dependencias listadas en `requirements.txt` (`fastapi`, `uvicorn[standard]`,
  `jinja2`, `python-multipart`).

## Instalación

```bash
git clone <url-del-repositorio>
cd NEXORA-CONTROL
```

## Instalación de dependencias

```bash
pip install -r requirements.txt
```

## Cómo iniciar la aplicación

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Al arrancar, la aplicación crea automáticamente el archivo de base de
datos y todas sus tablas si no existen todavía (no hace falta ningún
paso de migración manual).

## Cómo crear el primer administrador

La aplicación no crea ningún usuario por defecto. Para dar de alta el
primer usuario con acceso web (username + contraseña), ejecuta en tu
propia terminal:

```bash
python crear_admin.py
```

El script pide la contraseña de forma oculta (no se muestra en
pantalla ni queda guardada en ningún lado en texto plano: solo se
guarda su hash).

## Sobre la base de datos

- `MasterPlacas.db` **no se distribuye** junto con el código: está
  excluida del repositorio (ver `.gitignore`).
- Al clonar este repositorio en una máquina nueva, la aplicación crea
  una base de datos **nueva y vacía** la primera vez que se ejecuta.
- Los datos reales (usuarios, vehículos, sesiones, auditoría) son
  siempre locales a cada instalación y **no forman parte del
  repositorio** en ningún caso.
