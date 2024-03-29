import time
import math
from statistics import mode

import pandas as pd
from collections import deque, defaultdict

from datetime import datetime, timedelta
from enum import Enum

import requests
import pickle
import os


import threading
import logging

# configure logger
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('[%(asctime)s - %(name)s - %(levelname)s] %(message)s')
console_handler.setFormatter(formatter)
LOGGER.addHandler(console_handler)


class Team:

    def __init__(self, name, seed, code_name, odds_api_name, original_position):
        self.name = name
        self.seed = seed
        self.code_name = code_name
        self.odds_api_name = odds_api_name
        self.original_position = original_position

class Participant:

    def __init__(self, name, team):
        self.name = name
        self.team = team
        self.is_in = True
        self.history = []

    def to_str(self):
        return f"Name: {self.name}\tTeam: {self.team.name}\tStill In? {self.is_in}"


class Event:

    def __init__(self, round, event_id,
                 left=None, right=None, parent=None,
                 home_participant=None, away_participant=None
                 ):
        
        # round is an integer from 1 to math.log2(len(participants))
        self.round = round
        self.event_id = event_id

        # participants are only initialized for opening events because future matchups depend on previous results
        # home_participant will always be the winning_participant of the left event if one exists
        # thus, an event is either initalized with:
        #    1) home and away participants, if it's an opening event, OR
        #    2) left and right child events, if it's a future event
        assert (
            (home_participant is not None and away_participant is not None) or
            (left is not None and right is not None)
        )
        self.home_participant = home_participant
        self.away_participant = away_participant

        # an event is a node in a binary tree, where the next event is the "parent" node
        self.parent = parent
        self.left = left
        self.right = right

        # to populate as events occur:
        # spread info
        self.spread = None
        self.spread_final = False

        # espn game info: events can have 1 of 4 states:
        # 'STATUS_SCHEDULED', 'STATUS_FINAL', 'STATUS_IN_PROGRESS', 'TBD' (by default)
        self.status = 'TBD'
        self.team_to_score = {}  # dictionary of team code to integer
        self.is_complete = False
        self.winning_participant = None
        self.winning_team = None


        # example format: datetime(2024, 4, 1, 14, 0)  for event on on April 1, 2024, at 14:00
        self.estimated_start_time = None

    @property
    def matchup_tuple(self):
        home_team_code = self.home_participant.team.code_name
        away_team_code = self.away_participant.team.code_name
        return tuple(sorted([home_team_code, away_team_code]))  

    @property
    def is_scheduled(self):
        return self.matchup_determined and self.estimated_start_time is not None
    
    @property
    def matchup_determined(self):
        return (self.home_participant is not None and self.away_participant is not None)
    
    @property
    def is_starting_within_the_hour(self):
        current_time = datetime.now()
        time_difference = self.estimated_start_time - current_time
        return time_difference <= timedelta(hours=1) 

    def update(self, api_event_data):
        self.status = api_event_data['status']['type']['name']
        self.estimated_start_time = datetime.strptime(api_event_data['date'], '%Y-%m-%dT%H:%MZ')
        winning_team_code = None
        if 'competitions' in api_event_data:
            team_to_score = {}
            for i in range(2):
                score = api_event_data['competitions'][0]['competitors'][i]['score']
                team_code = api_event_data['competitions'][0]['competitors'][i]['team']['abbreviation']
                if 'winner' in api_event_data['competitions'][0]['competitors'][i]:
                    if api_event_data['competitions'][0]['competitors'][i]['winner']:
                        winning_team_code = team_code
                team_to_score[team_code] = score
            self.team_to_score = team_to_score
        self.is_complete = api_event_data['status']['type']['completed']
        if self.is_complete:
            self.winning_participant = self.determine_winning_participant()
            # optionally update the winning participant's team if their team lost but covered
            losing_team = self.home_participant.team if self.winning_participant is self.away_participant else self.away_participant.team
            if self.winning_participant.team.code_name != winning_team_code:
                self.winning_participant.team = losing_team
            self.winning_team = winning_team_code
            if self.parent is not None:
                self.parent.update_from_child(self)
    
    def determine_winning_participant(self):
        home_score = float(self.team_to_score[self.home_participant.team.code_name])
        home_score_delta = self.spread[self.home_participant.team.odds_api_name]
        away_score = float(self.team_to_score[self.away_participant.team.code_name])
        if (home_score + home_score_delta) > away_score:
            return self.home_participant
        elif (home_score + home_score_delta) < away_score:
            return self.away_participant
        else:
            # spread was even, return underdog
            return self.home_participant if home_score_delta > 0 else self.away_participant  
    
    def update_from_child(self, child):
        assert child.winning_participant is not None
        # update either the left or right child depending on which one was provided upon completion
        if child is self.left:
            self.home_participant = child.winning_participant
        else:
            assert child is self.right
            self.away_participant = child.winning_participant

    def to_str(self, as_html=False):
        if self.home_participant is not None:
            home_team = self.home_participant.team.name
            home_player = self.home_participant.name
            if self.home_participant.team.code_name in self.team_to_score:
                home_score = self.team_to_score[self.home_participant.team.code_name]
            else:
                home_score = 0
            home_str = f"{home_team} ({home_player})"
        else:
            home_score = 0
            home_str = f"Winner of Event # {self.left.event_id}"
        if self.away_participant is not None:
            away_team = self.away_participant.team.name
            away_player = self.away_participant.name
            if self.away_participant.team.code_name in self.team_to_score:
                away_score = self.team_to_score[self.away_participant.team.code_name]
            else:
                away_score = 0
            away_str = f"{away_team} ({away_player})"
        else:
            away_score = 0
            away_str = f"Winner of Event # {self.right.event_id}"

        spread_str = ""
        if self.spread is not None:
            for k, v in self.spread.items():
                if v < 0:
                    spread_str = f"{k} {v}"
        key_value_strings = (f'Event #: {self.event_id}', f'{home_str} vs. {away_str}',
                             f'Score: {home_score} - {away_score}', f'Spread: {spread_str}',
                             f'Status: {self.status}',
                             )
        status_to_color = {'STATUS_IN_PROGRESS': 'orange', 'STATUS_SCHEDULED': 'black', 'STATUS_FINAL': 'gray', 'TBD': 'purple'}
        status_color = status_to_color[self.status] if self.status in status_to_color else 'black'
        if as_html:
            html_str = ""
            for color, kv_string in zip(['red', 'blue', 'green', status_color, status_color], key_value_strings):
                html_str += f'<span style="color: {color};">{kv_string}</span>&nbsp;&nbsp;'
            return html_str
        else:
            return '\t'.join(key_value_strings)


class Bracket:

    class BracketCSVColumn(Enum):
        PARTICIPANT_NAME_COL = 'participant_name'
        TEAM_NAME_COL = 'team_name'
        SEED_COL = 'seed'
        CODE_NAME_COL = 'team_code'
        ODDS_API_TEAM_NAME_COL = 'odds_api_team_name'

    REQUIRED_KEYS = [key.value for key in BracketCSVColumn]
    SPREAD_CACHE_FILE = './.spread_cache.pkl'

    def __init__(self, participants, odds_api_key, cache_spread=True):
        # validate bracket -- currently only works with "even" brackets (e.g., 2, 4, 8, ... participants)
        assert math.log2(len(participants)).is_integer(), f'Only "evenly sized brackets (those with 4, 8, 16 ... 2^n participants.) ' +\
            f'are supported, but you have entered {len(participants)} participants.'
        assert len(participants) >= 4, f"Must have at least 4 participants, but you have {len(participants)}."
        self.participants = participants
        self.odds_api_key = odds_api_key

        # a bracket will be represented by a binary tree, and will be processed from the "bottom up" as games occur
        self.n_rounds = int(math.log2(len(participants)))
        self.results = [[]]

        # initialize first round of "events", assumes they are in "seeded" order, i.e.,
        # the first 2 participants will play eachother first, the winner of those 2 will play the winner of the next 2 and so on...
        ordered_events = []
        self.unique_events = 0
        for i in range(0, len(participants), 2):
            ordered_events.append(Event(
                round=1,
                event_id=self.unique_events+1,
                home_participant=participants[i],
                away_participant=participants[i+1],
            ))
            self.unique_events += 1

        # a bracket is represented by the root of a binary tree of events
        self.events_to_process = self.connect_bracket(ordered_events)
        assert len(self.events_to_process) == sum([2**n for n in range(self.n_rounds)])
        # print(f'\n\n{len(self.events_to_process)}\n\n')
        self.bracket_root = self.events_to_process[-1]  # last event will be the "root" or championship

        # failure mode tracking
        self.calls_to_espn = 0
        self.calls_to_odds_api = 0
        self.successfully_updating = True
        self.last_successful_update = None
        self.last_attempted_update = None

        # dedicated thread for the loop that updates bracket state indefinitely
        self._stop_event = threading.Event()
        self.update_thread = threading.Thread(target=self.process_indefinitely)
        self.update_thread.daemon = True  # Set the thread as daemon so it stops when we interrupt the process

        # Read/write odds from/to disk when available to avoid extra api calls
        # and slow startup time to pre-populate bracket. Will only cache spreads that are
        # final, i.e., games that have at least started alread. Useful when debugging :)
        if cache_spread:
            self.cache_spread = True
            LOGGER.info("Using spread cache!")
            if os.path.exists(self.SPREAD_CACHE_FILE):
                LOGGER.info(f"Existing cache file found at: {self.SPREAD_CACHE_FILE}")
                with open(self.SPREAD_CACHE_FILE, 'rb') as f:
                    self.matchup_to_spread = pickle.load(f)
            else:
                LOGGER.info(f"No cache file found at: {self.SPREAD_CACHE_FILE}. Creating one!")
                self.matchup_to_spread = {}
        else:
            self.cache_spread = False
    
    def get_state_metadata(self):
        return {
            'calls_to_epsn': self.calls_to_espn,
            'calls_to_odds_api': self.calls_to_odds_api,
            'is_successfully_updating': self.successfully_updating,
            'last_successful_update': str(self.last_successful_update),
            'last_attempted_update': str(self.last_attempted_update),
            'total_games_in_bracket': self.unique_events,
            'total_games_incomplete': len(self.events_to_process),
        }

    @property
    def events_in_progress(self):
        return [event for event in self.events_to_process if event.status == 'STATUS_IN_PROGRESS']

    def write_spreads_to_disk(self):
        if self.cache_spread:
            with open(self.SPREAD_CACHE_FILE, 'wb') as f:
                pickle.dump(self.matchup_to_spread, f)
    
    def connect_bracket(self, ordered_events):
        events_in_round = ordered_events
        all_events = []
        while len(events_in_round) > 1:
            assert len(events_in_round) % 2 == 0  # even number of matches in all rounds except the championship
            next_round_events = []
            all_events += events_in_round  # for returning the list of all outstanding events 
            for i in range(0, len(events_in_round), 2):
                left, right = events_in_round[i], events_in_round[i+1]
                assert left.round == right.round
                # initialize "parent" event in next round and connect to left and right children
                parent_event = Event(round=left.round + 1, event_id=self.unique_events+1, left=left, right=right)
                self.unique_events += 1  # don't forget to increment the number of events so they're properly ID'ed
                left.parent = parent_event
                right.parent = parent_event
                next_round_events.append(parent_event)
            events_in_round = next_round_events
        assert len(events_in_round) == 1  # only root node (championship) should remain
        return deque(all_events + events_in_round)

    def get_score_data(self, date_str=None):
        """
        date_str in format "YYYYMMDD"
        """
        data = requests.get(
            f'https://site.api.espn.com/' +\
            f'apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={date_str}'
        ).json()
        LOGGER.info(f'Data for {len(data["events"])} events returned.')
        # pre-process this data to make it query-able via two team codes
        event_data_by_matchup_tuple = {}
        for event_data in data['events']:
            matchup_tuple = tuple(sorted(event_data['shortName'].split(' VS ')))
            if matchup_tuple == ('TBD', 'TBD'):
                continue
            assert len(matchup_tuple) == 2
            assert matchup_tuple not in event_data_by_matchup_tuple  # teams should only play once in the tournament
            event_data_by_matchup_tuple[matchup_tuple] = event_data
        return event_data_by_matchup_tuple

    def set_event_spread(self, event, date_str):
        if self.cache_spread and event.matchup_tuple in self.matchup_to_spread:
                spread = self.matchup_to_spread[event.matchup_tuple]
        else:
            # call odds api
            spread = self.get_spread(event, date_str)
        event.spread = spread
            
        # if this event has already began, we can consider this spread final 
        if event.status == 'STATUS_FINAL' or event.status == 'STATUS_IN_PROGRESS':
                event.spread_final = True
        
        # maybe write new spread to cache
        if self.cache_spread:
            if event.matchup_tuple not in self.matchup_to_spread and spread is not None:
            # avoid caching spreads that were not found for some reason, just incase...
                self.matchup_to_spread[event.matchup_tuple] = spread
                self.write_spreads_to_disk()


    def get_spread(self, event, date_str):
        # determine if odds api event matches our event
        def team_match(game, event):
            if game['home_team'] == event.home_participant.team.odds_api_name:
                # ensure away matches away
                if game['away_team'] == event.away_participant.team.odds_api_name:
                    return True
            # away team matches home team
            if game['away_team'] == event.home_participant.team.odds_api_name:
                # ensure away matches home
                if game['home_team'] == event.away_participant.team.odds_api_name:
                    return True
            return False
        
        # given a list of spreads across bookmakers, return the most common spread     
        def spread_mode(spreads):
            spread_point_outcomes = [spread['outcomes'][0]['point'] for spread in spreads]
            point_spread = mode(spread_point_outcomes)
            return {
                spreads[0]['outcomes'][0]['name']: point_spread,  # home
                spreads[0]['outcomes'][1]['name']: -point_spread,  # away
            }

        # using historical odds api everywhere as it seems to still work for future events, in which case grabs the latest snapshot
        spreads_past = requests.get(
            f'https://api.the-odds-api.com/v4/historical/sports/basketball_ncaab/odds?' +\
            f'apiKey={self.odds_api_key}&regions=us&markets=spreads&dateFormat=iso&oddsFormat=american&date={date_str}'
        ).json()
        self.calls_to_odds_api += 1
        spreads = []
        for game in spreads_past['data']:
            # espn_to_odds_names = espn_team_to_odds_team(game, event)
            if team_match(game, event):
                for bookmaker in game['bookmakers']:
                    spreads.append(bookmaker['markets'][0])
                break
        return spread_mode(spreads) if len(spreads) > 0 else None
    
    def pre_populate_events(self):
        # TODO: replace this loop with process_indefinitely to backfill
        # query for data from march of the current year
        year = datetime.now().year
        # Wanted to query march + april with {year}0301-{year}0501, but this breaks things
        # because some earlier games are returned with shortName format "X @ Y" instead of 
        # the assumed "X VS Y". Could fix, but the larger issue is the weird behavior in the
        # set of games returned. I though querying for "202403" returned all games in march, 
        # but for some reason it returns only games from 3/20-3/30 right now (62 games).
        # On the other hand, querying "20240301-20240501" hits the apparent "limit" of 100 games, but
        # returns games from 3/01 - 3/09. I guess when a range is provided, it returns the games in
        # order of occurrance until the limit is hit. It may be that when only a month is provided,
        # it returns games in the opposite order (most recent first), but we didn't hit the 100 game
        # limit, so I have no idea why the "month only query" doesn't return those earlier games 
        # that break things ...
        event_data_by_matchup_tuple = self.get_score_data(f'{year}03')
        # maintain a queue of events that have home + away teams determined
        determined_events = deque([event for event in self.events_to_process if event.matchup_determined])
        while len(determined_events) > 0:
            event = determined_events.popleft()
            if event.matchup_tuple in event_data_by_matchup_tuple:
                event_data = event_data_by_matchup_tuple[event.matchup_tuple]
                # FIXME: if the game's available in ESPN much before odds-api, will this result in tons of API calls?
                if event.spread is None:
                    # populate initial spread 
                    time_before_game = (datetime.strptime(event_data['date'], '%Y-%m-%dT%H:%MZ') - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
                    self.set_event_spread(event, time_before_game)
                # maybe update
                old_status = event.status
                event.update(event_data)
                if old_status != event.status:
                    LOGGER.info(f'Status update for event from {old_status} to {event.status}.\n{event.to_str()}')
                    # update final spread if game moves to in progress
                    if event.status == 'STATUS_IN_PROGRESS':
                        time_before_game = (event.estimated_start_time - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
                        self.set_event_spread(event, time_before_game)

                        
                # the above update function will update the parent once games are over,
                # so let's check if the next round's game is determined and add it to the queue if so
                if event.parent.matchup_determined:
                    determined_events.append(event.parent)
                if event.is_complete:
                    self.events_to_process.remove(event)
        return

    @staticmethod
    def current_date_range_str():
        """
        Given the current time, we need to query for games yesterday/today/tomorrow,
        to avoid any timezone issues. The ESPN API expects a string formatted like this:
        YYYYMMDD-YYYYMMDD, but results are exclusive of the end date, so we format  the
        range from yesterday to 2 days from now.
        """
        current_date = datetime.now().date()
        start_date = current_date - timedelta(days=1)
        end_date = current_date + timedelta(days=2)
        start_date_str = start_date.strftime("%Y%m%d")
        end_date_str = end_date.strftime("%Y%m%d")
        return f"{start_date_str}-{end_date_str}"
    
    def should_query(self):
        events_starting_soon = [
            event for event in self.events_to_process if event.is_starting_within_the_hour
            and event.status == 'STATUS_SCHEDULED'
        ]
        return len(self.events_in_progress) > 0 and len(events_starting_soon) > 0
    
    def stop(self):
        self._stop_event.set()
        self.update_thread.join()
    
    def start(self):
        self.update_thread.start()
    
    def process_indefinitely(self):
        while self.events_to_process and not self._stop_event.is_set():
            if True:  # self.should_query(): TODO - add this optimization to significantly reduce api calls once status stuff tested 
                events_to_remove = []
                try:
                    current_event_data_by_matchup_tuple = self.get_score_data(self.current_date_range_str())
                    events_to_remove = []
                    for event in self.events_to_process:
                        # update
                        if event.matchup_determined and event.matchup_tuple in current_event_data_by_matchup_tuple:
                            event_data = current_event_data_by_matchup_tuple[event.matchup_tuple]
                            # FIXME: if the game's available in ESPN much before odds-api, will this result in tons of API calls?
                            if event.spread is None:
                                # populate initial spread 
                                time_before_game = (datetime.strptime(event_data['date'], '%Y-%m-%dT%H:%MZ') - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
                                self.set_event_spread(event, time_before_game)
                            # maybe update    
                            old_status = event.status
                            event.update(event_data)
                            if old_status != event.status:
                                LOGGER.info(f'Status update for event from {old_status} to {event.status}.\n{event.to_str()}')
                                # update final spread if game moves to in progress
                                if event.status == 'STATUS_IN_PROGRESS':
                                    time_before_game = (event.estimated_start_time - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
                                    self.set_event_spread(event, time_before_game)
                        if event.is_complete:
                            events_to_remove.append(event)
                    
                    # count successful iterations
                    self.calls_to_espn += 1
                    self.successfully_updating = True
                    self.last_successful_update = datetime.now()
                
                except Exception:
                    self.successfully_updating = False
                    LOGGER.exception('Oh fuck...')
            
                # remove events that completed this iteration
                for event in events_to_remove:
                    self.events_to_process.remove(event)

                self.last_attempted_update = datetime.now()
            # Sleep for 60 seconds, but check for stop event every second
            for _ in range(60):
                if self._stop_event.is_set():
                    break
                time.sleep(1)
        LOGGER.info('Bracket complete!')
        return

    def round_description(self, round):
        teams_left_to_round_name = {
            2: "Championship",
            4: "Semi-Finals",
            8: "Quarter-Finals"
        }
        teams_left = int(len(self.participants) / 2 ** (round - 1))
        if teams_left in teams_left_to_round_name:
            return teams_left_to_round_name[teams_left]
        else:
            return f"Round of {teams_left}"
    
    
    def to_events_by_round(self):
        events_by_round = defaultdict(list)
        queue = deque()
        queue.append(self.bracket_root)
        while queue:
            event = queue.popleft()
            events_by_round[event.round].append(event)
            if event.left:
                queue.append(event.left)
            if event.right:
                queue.append(event.right)
        assert all([round in events_by_round for round in range(1, self.n_rounds+1)])
        return events_by_round

    def to_str(self, as_html=False):
        """
        Pop all events and concatenate line-delimited strings by round.
        """
        bracket_as_str = ""
        events_by_round = self.to_events_by_round()
        for round in range(self.n_rounds, 0, -1):
            bracket_as_str += '\n' * 3 + '=' * 50 + self.round_description(round) + '=' * 50 + '\n'
            for event in events_by_round[round]:
                bracket_as_str += event.to_str(as_html=as_html) + '\n'
        return bracket_as_str
    
    def pretty_print(self):
        print(self.to_str())   

    @classmethod
    def from_config(cls, config):
        odds_api_key = config['ODDS_API_KEY']
        participant_df = pd.read_csv(config['TEAM_CSV_PATH'])
        assert all([key in participant_df.columns for key in cls.REQUIRED_KEYS])
        participants = []
        for i, row in participant_df.iterrows():
            team = Team(
                row[cls.BracketCSVColumn.TEAM_NAME_COL.value],
                row[cls.BracketCSVColumn.SEED_COL.value],
                row[cls.BracketCSVColumn.CODE_NAME_COL.value],
                row[cls.BracketCSVColumn.ODDS_API_TEAM_NAME_COL.value],
            original_position=i,
            )
            participant = Participant(row[cls.BracketCSVColumn.PARTICIPANT_NAME_COL.value], team)
            participants.append(participant)
        return cls(participants, odds_api_key)
