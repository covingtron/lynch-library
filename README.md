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
python manage.py fetch_podcast_episode 2
```

The command streams the origin response to R2 without creating a local audio file. Without an
episode number it fetches the oldest episode missing from R2; the Fetch workflow does this daily.
Until the `R2_URL` repository secret is set, `R2_URL` defaults to `off` and the command warns and
exits without fetching.

This project aims to catalog information about [Lynch syndrome](https://en.wikipedia.org/wiki/Lynch_syndrome), also known as DNA mismatch repair deficiency. This project is entirely AI generated.

## Contributing

When contributing, run `git commit -a --amend` to avoid adding temporary local files and keep the git commit log meaningful and navigable (avoid many small commits, and especially intermediate breakage).

See `TODO.md` for development roadmap.
