import json

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
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
