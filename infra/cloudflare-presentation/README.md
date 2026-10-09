# Publish the standalone presentation

Public URL: [https://memory-gateway-demo.ashishranjan2404.workers.dev](https://memory-gateway-demo.ashishranjan2404.workers.dev).

The UI is hosted as a Cloudflare Worker with static assets only. There is no server-side Worker bundle, credential binding, or connection to the gateway. The deployment copies exactly these five public assets: `index.html`, `styles.css`, `app.js`, `scenario.js`, and `evidence.js`.

The staging script uses the Build Output Specification accepted by `cf` version `1.0.0-beta.14`. The Cloudflare CLI is in beta; run the dry run before uploading after a CLI update. Generated files and the SHA-256 asset manifest live under ignored `build/cloudflare-presentation/`, outside the public asset directory.

From the repository root:

```sh
.venv/bin/python infra/cloudflare-presentation/prepare.py
cd build/cloudflare-presentation
cf deploy --prebuilt --dry-run
cf deploy --prebuilt --message 'Publish illustrated memory gateway incident player'
```

Use the existing Cloudflare CLI login or an API token with the required Workers permissions. `CLOUDFLARE_API_TOKEN` takes precedence over the saved browser login. If an existing token lacks deployment permission, complete `cf auth login` and run deployment with that environment variable omitted for the command:

```sh
env -u CLOUDFLARE_API_TOKEN cf deploy --prebuilt
```

Set `CLOUDFLARE_ACCOUNT_ID` explicitly when more than one account is available. Never place tokens in this directory, source code, or public assets.

The Worker is named `memory-gateway-demo`. `workers.dev` access is enabled, version preview URLs are disabled, and unknown paths return 404 rather than the application page. The CLI deployment output supplies the public HTTPS URL.

After publishing, verify all five served assets match the local SHA-256 hashes, then exercise the baseline, protected reset, poisoned-skill inspector, and recorded-evidence dialog in a browser. Check that `/.env`, `/README.md`, and `/asset-manifest.json` return 404.

## Verified deployment

Version `878e6cc6-3a06-44cb-b414-289f290c163c` was published on October 9, 2026. All five HTTPS assets matched the local SHA-256 hashes; `/.env`, `/README.md`, `/asset-manifest.json`, and `/.gateway-admin.json` return 404. A deployed browser smoke passed 22 checks, including both scenario modes, reset, dialogs, and mobile layout. Verification artifacts stay in ignored `build/`.
