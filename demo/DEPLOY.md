# Akash demo deployment

The demo output page is deployed on Akash (decentralized compute), serving the before/after story publicly.

- **Live URL:** http://67s3r5os51agp42v6tfsi3fhsc.ingress.zencloud.eu
- **SDL:** `demo/akash-demo.yaml` (python:3.12-slim, fetches `demo/output/index.html` from the repo, serves on :80)
- **Deployed via:** Akash Console API (managed wallet, `x-api-key`). dseq `1791580242440`.
- Redeploy/patch/close with the Console API; see the akash skill.
