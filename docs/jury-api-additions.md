# Jury API additions

## Public image editing

The frontend shows originals initially and applies name mosaics when the pencil button is used. The backend stores the resulting image; it does not detect names or generate mosaics.

Authenticated owner endpoint: `/api/cases/{case_id}/images/{image_id}/public-image/`

- GET: preview edited image, or original if no edit exists.
- PUT: multipart `image` file (PNG/JPEG/WEBP); same per-image size/pixel limits as original uploads. Returns the image metadata.
- DELETE: discard edit and use original again (204).

Case image metadata now includes `has_public_image` and `public_image_url` (owner authentication required). Original `image_key` remains unchanged. Jury image URLs and thumbnails automatically serve the edited image if present, otherwise original. Upload all edited images successfully before publishing. Edits to an already-public case take effect immediately. There is no requirement to upload edited copies of untouched images. Existing public cases remain public.

## My activity

GET `/api/jury/me/activities/?type=all&page=1` (authenticated).

`type`: `all` (default), `votes`, `comments`. Invalid types return 400. Page size: 20. One row per case, ordered by latest vote or visible-comment creation time, descending.

Standard pagination: `count`, `next`, `previous`, `results`.

Each result: `case_id`, `title`, `relation`, `thumbnail`, `has_voted`, `my_fault_ratio` (author fault percent or null), `my_comment_count`, `last_activity_at`.

Only currently public, judged, non-hidden, non-deleted cases are included. Hidden/deleted comments do not count. Vote participation remains even if the comment is removed. Comment edits do not change activity order.

## Relations

Creation and jury filtering use the same `cases.relations.RELATION_CHOICES` definition. Legacy school/team-project relationship values in situation and verdict relation columns are migrated to the canonical workplace/school value. Analysis input snapshots remain historical records.

Deploy with `python manage.py migrate --noinput` before starting web/worker services.


Public image edits enforce the total size limit across effective jury images (edited copy when present, otherwise original). Replacing/resetting an image creates a durable deletion task, attempted after commit. Schedule `python manage.py retry_image_deletions` periodically on a service with the same DB and storage credentials to retry failed deletions. Pending rows are retained until deletion succeeds.
