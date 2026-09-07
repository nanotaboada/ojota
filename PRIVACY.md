# Política de privacidad — ojota

_Última actualización: 2026-09-07_

**ojota** es una herramienta personal y de código abierto que se ejecuta en
la computadora del propio usuario. Detecta movimiento en una cámara IP de su
red local, graba clips de video y los sube a la cuenta de Google Drive de ese
mismo usuario.

## Qué datos se manejan

- **Video y audio** capturados por la cámara IP del usuario, en su propia red
  local.
- Estos clips se suben **exclusivamente a la cuenta de Google Drive del
  usuario que instala y configura la herramienta**, mediante la API de Google
  Drive con el permiso `drive.file` (acceso únicamente a los archivos que la
  propia herramienta crea).

## Quién accede a los datos

- Solo el usuario. La herramienta corre localmente y sube el contenido a la
  cuenta de Drive de ese usuario.
- Los autores de ojota **no reciben, no almacenan y no tienen acceso** a
  ningún dato, video, credencial ni token.
- No hay servidores intermedios, analítica, ni terceros.

## Uso de la API de Google

ojota usa la API de Google Drive solo para: crear una carpeta de destino,
subir archivos de video, generar enlaces para compartir esos archivos y
borrar archivos antiguos según la política de retención que el usuario
configure. El token de acceso se guarda localmente en la máquina del usuario
(`~/.config/rclone/rclone.conf`) con permisos restringidos.

El uso de la información obtenida de las APIs de Google se ajusta a la
[Política de Datos del Usuario de los Servicios de API de Google](https://developers.google.com/terms/api-services-user-data-policy),
incluidos sus requisitos de Uso Limitado.

## Contacto

Para consultas, abrí un issue en
<https://github.com/nanotaboada/ojota/issues>.
