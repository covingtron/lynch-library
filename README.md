# Lynch Library

## Archive a podcast episode

Provision the private `library` bucket:

```bash
source $SECRETS_FILE
tofu -chdir=deploys/library/terraform init
tofu -chdir=deploys/library/terraform apply
```

Create a bucket-scoped Object Read & Write credential outside Terraform state. The parent
`CLOUDFLARE_API_TOKEN` needs Account API Tokens Write permission:

```bash
umask 077
python -m scripts.create_r2_service_credential > $R2_SECRETS_FILE
source $R2_SECRETS_FILE
python manage.py fetch_podcast_episode --period-minutes=360
```

The command streams the origin response to R2 without creating a local audio file. It reads R2 to
find the newest archived month, moving on to the next month while R2 already holds every episode
listed, and saves the oldest episode still missing; `--episode-number` picks another one from that
month. Until the `R2_URL` repository secret is set, `R2_URL` defaults to `off` and the command
warns and exits without fetching.

Every run reports how many episodes R2 holds against an estimated total, reading only the archive
pages it walks. Months before the one reached count as complete, that month counts its links, and
later months average the known ones through the current month. `--period-minutes` tells the command
how often the fetch workflow runs, to forecast completion; the workflow passes the 360 minutes of
its six-hourly schedule.

This project aims to catalog information about [Lynch syndrome](https://en.wikipedia.org/wiki/Lynch_syndrome), also known as DNA mismatch repair deficiency. This project is entirely AI generated.

## Contributing

When contributing, run `git commit -a --amend` to avoid adding temporary local files and keep the git commit log meaningful and navigable (avoid many small commits, and especially intermediate breakage).

See `TODO.md` for development roadmap.
