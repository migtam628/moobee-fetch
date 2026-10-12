# Create the MooBee Fetch GitHub repository

Repository: **migtam628/moobee-fetch** (recommended **private**).

## 1. Create an empty private repository

At https://github.com/new, enter `moobee-fetch` under `migtam628`, set **Private**, and leave README, gitignore, and license **unchecked**. The code package already contains a README and gitignore.

## 2. Push only the project sources from your Mac

Extract the provided `moobee-fetch-v0.1-github-ready.zip`, open a terminal in `moobee-fetch-v0.1`, and run:

```sh
git init -b main
git add .
git status --short
# Before committing, verify .env and data/ are NOT listed.
git commit -m 'feat: initial isolated MooBee Fetch prototype'
git remote add origin git@github.com:migtam628/moobee-fetch.git
git push -u origin main
```

If you prefer HTTPS auth, change the remote to `https://github.com/migtam628/moobee-fetch.git`. Follow GitHub's credential prompts; never paste credentials into ChatGPT. CI automatically validates Python, Bash, Compose and the two Docker builds.

## 3. Do not deploy without explicit approval

There is no active VPS deployment. The Stream VPS hosts existing MooBee Stream and test IPTV services. The prototype is a separate Docker Compose project; deployment should not modify, restart, or reconfigure those projects. Torrent use discloses the VPS public IP to peers; confirm provider rules and outbound network policy first.
