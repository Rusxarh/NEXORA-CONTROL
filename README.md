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

## Cómo encender todo (panel + NEXCAM + Telegram)

Para el uso normal (no desarrollo), hazle doble clic a **`iniciar-nexora.bat`**
en vez de escribir el comando de arriba. Ese lanzador:

- revisa el puerto 8000: si lo tiene el servidor de prueba viejo de NEXCAM
  (`node.exe`), lo cierra solo; si ya hay otra NEXORA-CONTROL encendida,
  no abre una segunda, solo te lleva al navegador; si lo tiene cualquier
  otro programa, se detiene y te dice cuál es, sin tocarlo;
- instala las dependencias si faltan (usa el Python de `.venv` si existe);
- abre el navegador en `http://localhost:8000` apenas el servidor responde;
- muestra la dirección para el teléfono (tu IP de Wi-Fi), y si el bot de
  Telegram quedó activo, el código para vincular un chat nuevo;
- avisa si falta la regla de firewall de Windows para el puerto 8000 (no
  la crea sola: requiere permisos de Administrador).

**No cierres esa ventana**: el servidor corre ahí mismo, así que cerrarla
lo apaga.

Para dejar un acceso directo en el Escritorio, ejecuta una sola vez
`crear-acceso-directo.ps1` (click derecho → "Ejecutar con PowerShell").

### Bot de Telegram (opcional)

Si quieres que NEXCAM avise por Telegram, crea un archivo `telegram.json`
junto a `main.py` con tu token de [@BotFather](https://t.me/BotFather):

```json
{ "token": "123456789:TU-TOKEN-AQUI" }
```

(o define la variable de entorno `TELEGRAM_TOKEN`). Ese archivo nunca se
sube al repositorio (ver `.gitignore`). Sin un token válido, el bot queda
desactivado y el resto de NEXORA-CONTROL funciona exactamente igual.

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
