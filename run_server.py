import json
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager

from bracket import Bracket
from dotenv import dotenv_values

config = dotenv_values(".env")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize bracket on startup
    print('running lifespan')
    global bracket
    bracket = Bracket.from_config(config)
    bracket.pretty_print()
    
    # pre-populate to fill games that have already happened
    bracket.pre_populate_events()
    print(f'\nWith events populated:')
    bracket.pretty_print()

    # run update loop in its own thread
    bracket.start()
    
    yield  # hold up until we done
    bracket.stop()
    bracket.write_spreads_to_disk()

app = FastAPI(lifespan=lifespan)

# Mount static files directory
app.mount("/static", StaticFiles(directory="static"), name="static")

# Set up Jinja2 templates
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the main bracket view"""
    # Get the metadata directly from the bracket class method
    metadata = bracket.get_state_metadata()
    
    # Use the existing to_events_by_round method to get events organized by round
    events_by_round = bracket.to_events_by_round()
    
    # Flatten the events for template use
    events = []
    for round_num, round_events in events_by_round.items():
        for event in round_events:
            events.append(event)
    
    # Pass the bracket data, including events and the bracket itself for helper methods
    return templates.TemplateResponse(
        "index.html", 
        {
            "request": request, 
            "metadata": metadata, 
            "bracket_data": {
                "n_rounds": bracket.n_rounds,
                "events": events
            },
            "bracket": bracket  # Pass the bracket object for access to methods like round_description
        }
    )

@app.get("/api/print-bracket")
def get_bracket_as_string():
    metadata_str = 'CURRENT STATE:\n' + json.dumps(bracket.get_state_metadata(), indent=4) + '\n'
    string_to_print = metadata_str + bracket.to_str(as_html=True)
    # format tabs and newlines as html
    html_content = "<pre>{}</pre>".format(
        string_to_print.replace("\n", "<br>").replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;")
    )
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/api/bracket-as-json")
def get_bracket_json():
    to_return = bracket.model_dump(mode='json', exclude='participants')  # participants is redundant
    return JSONResponse(content=to_return, status_code=200)