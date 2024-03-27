# madness
Manager (and hopefully UI) for Mike's shitty march madness bracket.

## Quickstart
I recommend you manage python via miniconda ([how to install](https://docs.anaconda.com/free/miniconda/#quick-command-line-install)). 
```
conda create -y -n madness python=3.8
conda activate madness
pip install -r requirements.txt
python construct_bracket.py mm-with-team-codes.csv
```

## TODO
- [ ] Add sportsbook ruling
- [ ] Add UI, ideally from [here](https://github.com/Drarig29/brackets-viewer.js?tab=readme-ov-file), but I don't know javascript :o
- [ ] Implement and test updating logic
- [ ] 1 million other things in my life
- [ ] Add UI for bracket creation so that user doesn't need to provide a CSV file
