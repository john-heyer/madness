# madness
Manager (and hopefully UI) for Mike's shitty march madness bracket.

## Quickstart

### Python Environment
I recommend managing Python with pyenv and Poetry for this project. Here's how to set up:

1. Install [pyenv](https://github.com/pyenv/pyenv) to manage Python versions:
  ```bash
   brew install pyenv
   echo 'eval "$(pyenv init --path)"' >> ~/.zshrc
   echo 'eval "$(pyenv init -)"' >> ~/.zshrc
   source ~/.zshrc
  ```

2. Install Poetry for dependency management:
  ```bash
  curl -sSL https://install.python-poetry.org | python3 -
  ```

3. (Optional) Install direnv for automatic environment activation:
  ```bash
  brew install direnv
  echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc
  source ~/.zshrc
  ```

4. (Optional) Add the following to your `direnv` config so it knows how to layout a poetry project:
  ```bash
  # Create the direnv config directory if it doesn't exist
  mkdir -p ~/.config/direnv

  # Create or append to the direnvrc file
  cat > ~/.config/direnv/direnvrc << 'EOF'
  layout_poetry() {
      PYPROJECT_TOML="${PYPROJECT_TOML:-pyproject.toml}"
      if [[ ! -f "$PYPROJECT_TOML" ]]; then
          log_status "No pyproject.toml found. Executing \`poetry init\` to create a \`$PYPROJECT_TOML\` first."
          poetry init
      fi

      if [[ -d ".venv" ]]; then
          VIRTUAL_ENV="$(pwd)/.venv"
      else
          VIRTUAL_ENV=$(poetry env info --path 2>/dev/null ; true)
      fi

      if [[ -z $VIRTUAL_ENV || ! -d $VIRTUAL_ENV ]]; then
          log_status "No virtual environment exists. Executing \`poetry install\` to create one."
          poetry install
          VIRTUAL_ENV=$(poetry env info --path)
      fi

      PATH_add "$VIRTUAL_ENV/bin"
      export POETRY_ACTIVE=1
      export VIRTUAL_ENV
  }
  EOF
  ```

5. (Optional) Add virtual environment name to your prompt by adding this to your ~/.zshrc:
  ```bash
  # Add active virtual env name to command line prompt
  show_virtual_env() {
    if [[ -n "$VIRTUAL_ENV" && -n "$DIRENV_DIR" ]]; then
      echo "($(basename $VIRTUAL_ENV)) "
    fi
  }
  PS1='$(show_virtual_env)'$PS1

  # Source the updated zshrc
  source ~/.zshrc
  ```

### Setup Project

```bash
# Clone the repository
git clone [repository URL]
cd madness

# Install the correct Python version
pyenv install 3.12.0
pyenv local 3.12.0

# Install dependencies
poetry install

# Allow direnv to automatically activate the environment
direnv allow
```
Now whenever you navigate to the project directory, the Python environment will be automatically activated. You can verify this by checking that your terminal prompt shows the environment name and by running `which python`.

### Odds API Key
Add your [odds-api key](https://the-odds-api.com/#get-access) and path to csv with team data to the `.env` file in this directory (there's already one committed in this repo and set in th `.env` by default).

### Run Server locally
The server is setup using [Fast API](https://fastapi.tiangolo.com/#run-it), and will run on your localhost via:
```
uvicorn run_server:app
```

Navigate to http://127.0.0.1:8000/api/print-bracket to view the current bracket! It currently makes an API call to ESPN every minute, and only calls odds API for each game once upon scheduling, and again as soon as the game begins to try to get the opening spread.

## Populating Bracket CSV

Follow instructions in bracket-setup.ipynb


## Endpoints
**Main bracket view**: http://ec2-54-173-62-3.compute-1.amazonaws.com/

`/api/print-bracket`:
- prints the bracket as raw-but-prettified HTML

`/api/bracket-as-json`:
- returns the bracket as nested json, with the fields defined [here](https://github.com/john-heyer/madness/blob/main/bracket.py#L225-L239). Importantly, `"bracket"` is the root node (or `Event`) of the tree, with the fields defined [here](https://github.com/john-heyer/madness/blob/main/bracket.py#L49-L72). This json is huge, open up chrome with "pretty-print" on your local host here: http://127.0.0.1:8000/api/bracket-as-json (also currently available on an ec2 machine here: http://ec2-54-173-62-3.compute-1.amazonaws.com/, but prob not for long).

## How do we get scores and lines?
[ESPN API](https://github.com/pseudo-r/Public-ESPN-API) for scores, [odds-api](https://the-odds-api.com/) for lines.


## Quick Reference: March Madness Bracket Setup on EC2
### Initial Setup (Already Done)

1. Connect to EC2: `ssh -i ~/.ssh/john-personal.pem ubuntu@ec2-54-173-62-3.compute-1.amazonaws.com`
2. Install dependencies: `sudo apt update && sudo apt install nginx python3 python3-pip git`
3. Install Poetry: `curl -sSL https://install.python-poetry.org | python3 -`
4. Clone repo: `git clone [repository URL]`

#### FastAPI App Setup

1. Update environment variables (`.env` file)
2. Install dependencies: `cd madness && poetry install`
3. Create systemd service:
```bash
sudo nano /etc/systemd/system/bracket-tracker.service
# Add service config
sudo systemctl enable bracket-tracker
sudo systemctl start bracket-tracker
```

#### Nginx Configuration

1. Create Nginx config:
```bash
sudo nano /etc/nginx/sites-available/bracket
# Add server config with proxy to FastAPI (should be there already)
```
2. Enable site:
```bash
sudo ln -s /etc/nginx/sites-available/bracket /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
```

3. Check config: `sudo nginx -t`
4. Restart Nginx: `sudo systemctl restart nginx`
5. Ensure nginx can access static files:
```bash
sudo chmod 755 /home/ubuntu/madness/static
sudo chmod 644 /home/ubuntu/madness/static/styles.css
```

### For Next Year (Refresh Process)

1. Reboot and connect to EC2: `ssh -i ~/.ssh/john-personal.pem ubuntu@ec2-54-173-62-3.compute-1.amazonaws.com`
2. Pull latest code: `cd madness && git pull`
3. Update dependencies: `poetry install`
5. Restart services:
```bash
sudo systemctl restart bracket-tracker
sudo systemctl restart nginx
```

6. Confirm app is running: http://your-ec2-ip/

### Common Troubleshooting

1. Check FastAPI logs: `sudo journalctl -u bracket-tracker -f`
2. Check Nginx logs: `sudo tail -f /var/log/nginx/error.log`
3. Check permissions if CSS not loading
4. Test direct access: `curl http://localhost:8000/`

#### References
* Claude [chat](https://claude.ai/share/4e2b01b5-07c9-43c5-866a-9ba22e48e4a2)
* Originally followed this [blog](https://medium.com/@vanyamyshkin/deploy-python-fastapi-for-free-on-aws-ec2-050b46744366) to get it running on ec2.

## TODO
- [ ] Clean up and test updating logic
- [ ] Add script to re-deploy on ☁️
- [ ] 1 million other things in my life
- [ ] Add UI for bracket creation so that user doesn't need to provide a CSV file
