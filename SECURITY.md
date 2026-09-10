# Security

## Alcance

ojota es un proyecto personal para una sola instalación. No hay servidor
ni servicio compartido: cada quien corre su propia copia en su Mac.

## Datos sensibles

- **Contraseña RTSP de la cámara** y **topic de ntfy**: viven solo en
  `config/ojota.conf`, que está en `.gitignore` y con permisos `600`.
- **Token de OAuth de Google Drive**: en `config/rclone.conf` (copia de
  root, `600`) o en `~/.config/rclone/rclone.conf`. Scope `drive.file`:
  rclone solo accede a los archivos que crea.
- El backup de configuración (`CONFIG_BACKUP=1`) sube estos archivos a
  `gdrive:ojota/config-backup/`. **Mantené esa carpeta privada.**
- Los videos en Drive quedan accesibles por link (`rclone link`) para
  poder abrirlos desde la notificación. Cualquiera con el link los ve.

Nunca pegues un token ni una contraseña en un issue o PR. Si compartís
logs, redactá la URL RTSP (`rtsp://***@…`).

## Reportar una vulnerabilidad

Abrí un [Security Advisory privado](https://github.com/nanotaboada/ojota/security/advisories/new)
o un issue sin detalles de explotación y pedí un canal privado.
