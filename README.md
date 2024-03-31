# madness
Manager (and hopefully UI) for Mike's shitty march madness bracket.

## Quickstart
I recommend you manage python via miniconda ([how to install](https://docs.anaconda.com/free/miniconda/#quick-command-line-install)). 
```
conda create -y -n madness python=3.8
conda activate madness
pip install -r requirements.txt
```

Add your [odds-api key](https://the-odds-api.com/#get-access) and path to csv with team data to the `.env` file in this directory (there's already one committed in this repo and set in th `.env` by default).

The server is setup using [Fast API](https://fastapi.tiangolo.com/#run-it), and will run on your localhost via:
```
uvicorn run_server:app
```

Navigate to http://127.0.0.1:8000/api/print-bracket to view the current bracket! It currently makes an API call to ESPN every minute, and only calls odds API for each game once upon scheduling, and again as soon as the game begins to try to get the opening spread.

## Endpoints
`/api/print-bracket`:
- prints the bracket as HTML

`/api/bracket-as-json`:
- returns the bracket as nested json, with the fields defined [here](https://github.com/john-heyer/madness/blob/main/bracket.py#L225-L239). Importantly, `"bracket"` is the root node (or `Event`) of the tree, with the fields defined [here](https://github.com/john-heyer/madness/blob/main/bracket.py#L49-L72). This json is huge, open up chrome with "pretty-print" on your local host here: http://127.0.0.1:8000/api/bracket-as-json (also currently available on an ec2 machine here: http://ec2-18-208-184-64.compute-1.amazonaws.com/api/bracket-as-json, but prob not for long).

## How do we get scores and lines?
[ESPN API](https://github.com/pseudo-r/Public-ESPN-API) for scores. Looking at [odds-api](https://the-odds-api.com/) for lines.


## TODO
- [ ] Add UI, ideally from [here](https://github.com/Drarig29/brackets-viewer.js?tab=readme-ov-file), but I don't know javascript 🤡. I assume we just need this app to return the bracket in json form compatible with the dude's [model](https://github.com/Drarig29/brackets-model).
- [ ] Clean up and test updating logic
- [ ] Add script to run on ☁️
- [ ] 1 million other things in my life
- [ ] Add UI for bracket creation so that user doesn't need to provide a CSV file
