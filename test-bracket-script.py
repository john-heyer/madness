from bracket import Bracket
from dotenv import dotenv_values

config = dotenv_values(".env")

bracket = Bracket.from_config(config)
bracket.pretty_print()

# pre-populate to fill games that have already happened
bracket.pre_populate_events()
print(f'\nWith events populated:')
bracket.pretty_print()

# run update loop
bracket.process_indefinitely()
