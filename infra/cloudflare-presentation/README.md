# Publish the standalone presentation

Public URL: [https://memory-gateway-demo.ashishranjan2404.workers.dev](https://memory-gateway-demo.ashishranjan2404.workers.dev).

The UI is hosted as a Cloudflare Worker with static assets only. There is no server-side Worker bundle, credential binding, or connection to the gateway. The deployment copies exactly these five public assets: `index.html`, `styles.css`, `app.js`, `scenario.js`, and `evidence.js`.

The staging script uses the Build Output Specification accepted by `cf` version `1.0.0-beta.14`. The Cloudflare CLI is in beta; run the dry run before uploading after a CLI update. Generated files and the SHA-256 asset manifest live under ignored `build/cloudflare-presentation/`, outside the public asset directory.

From the repository root:

```sh
.venv/bin/python infra/cloudflare-presentation/prepare.py
cd build/cloudflare-presentation
cf deploy --prebuilt --dry-run
cf deploy --prebuilt --message 'Publish recorded model failures and verified gateway evidence'
```

Use the existing Cloudflare CLI login or an API token with the required Workers permissions. `CLOUDFLARE_API_TOKEN` takes precedence over the saved browser login. If an existing token lacks deployment permission, complete `cf auth login` and run deployment with that environment variable omitted for the command:

```sh
env -u CLOUDFLARE_API_TOKEN cf deploy --prebuilt
```

Set `CLOUDFLARE_ACCOUNT_ID` explicitly when more than one account is available. Never place tokens in this directory, source code, or public assets.

The Worker is named `memory-gateway-demo`. `workers.dev` access is enabled, version preview URLs are disabled, and unknown paths return 404 rather than the application page. The CLI deployment output supplies the public HTTPS URL.

After publishing, verify all five served assets match the local SHA-256 hashes, then exercise the model and protected sequences, reset, poisoned-skill inspector, all four evidence tabs, receipt selector, and evidence download in a browser. Check that `/.env`, `/README.md`, and `/asset-manifest.json` return 404.

## Verified deployment

Version `7138f4d7-3a0b-4bf6-875f-9fff067bc607` was published on October 9, 2026 Pacific. It replaces the illustrated story with recorded GPT-OSS-20B native tool calls, three live gateway replay receipts, and nine verified ClickHouse events. The unchanged static-only deployment has no backend bindings or credentials. Local browser QA passed 590 checks; the deployed browser smoke passed 45. All five served asset hashes match the local files, and the four private/config paths checked return 404. Checks cover evidence equality, both sequences, actual event chronology, downloads, tabs, and mobile layout. Public HTTP and browser verification artifacts stay in ignored `build/`.
