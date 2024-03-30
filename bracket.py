from __future__ import annotations

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

from pydantic import BaseModel
from typing import List, Dict, Optional, Literal
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


class Team(BaseModel):
    name: str
    seed: int
    code_name: str
    odds_api_name: str
    original_position: int


class Participant(BaseModel):
    name: str
    team: Team
    is_in: bool = True

    def to_str(self):
        return f"Name: {self.name}\tTeam: {self.team.name}\tStill In? {self.is_in}"


class Event(BaseModel):
    # round is an integer from 1 to math.log2(len(participants))
    round: int
    event_id: int

    # only non-null after the previous round's game is complete
    home_participant: Optional[Participant] = None
    away_participant: Optional[Participant] = None
    
    # children event "nodes" from previous round
    left: Optional[Event] = None
    right: Optional[Event] = None

    # Everything below to populate as events occur:
    # spread info
    spread: Optional[Dict[str, float]] = None
    spread_final: bool = False

    # espn game info - score and status taken directly from their api
    status: Literal['STATUS_SCHEDULED', 'STATUS_IN_PROGRESS', 'STATUS_HALFTIME', 'STATUS_FINAL', 'TBD'] = 'TBD'
    team_to_score: Dict[str, int] = {}  # dictionary of team code to integer
    is_complete: bool = False
    winning_participant: Optional[Participant] = None
    winning_team_code: Optional[str] = None

    # example format: datetime(2024, 4, 1, 14, 0)  for event on on April 1, 2024, at 14:00
    estimated_start_time: Optional[datetime] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # define the parent pointer privately so it's ignored by pydantic.
        # otherwise, this would lead to a circular reference and ultimately 
        # an infinite loop upon attempting to dump to the pydantic model to json or whatever
        self._parent = None
    
    @classmethod
    def first_round_event(cls, event_id, home_participant, away_participant):
        return cls(
                round=1,
                event_id=event_id,
                home_participant=home_participant,
                away_participant=away_participant,
        )

    @classmethod
    def from_children(cls, event_id, left, right):
        return cls(
            round=left.round + 1,
            event_id=event_id,
            left=left,
            right=right
        )

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
            self.winning_team_code = winning_team_code
            if self._parent is not None:
                self._parent.update_from_child(self)
    
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
                # only show the handicapped team
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


class BracketCSVColumn(Enum):
    PARTICIPANT_NAME_COL = 'participant_name'
    TEAM_NAME_COL = 'team_name'
    SEED_COL = 'seed'
    CODE_NAME_COL = 'team_code'
    ODDS_API_TEAM_NAME_COL = 'odds_api_team_name'


REQUIRED_KEYS = [key.value for key in BracketCSVColumn]
SPREAD_CACHE_FILE = './.spread_cache.pkl'


class Bracket(BaseModel):

    participants: List[Participant]

    n_rounds: int
    n_unique_events: int

    bracket_root: Event

    # failure mode tracking
    calls_to_espn: int = 0
    calls_to_odds_api: int = 0
    successfully_updating: bool = True
    last_successful_update: Optional[datetime] = None
    last_attempted_update: Optional[datetime] = None




    def __init__(self, participants, odds_api_key, cache_spread=True):
        # validate bracket -- currently only works with "even" brackets (e.g., 2, 4, 8, ... participants)
        assert math.log2(len(participants)).is_integer(), f'Only "evenly sized brackets (those with 4, 8, 16 ... 2^n participants.) ' +\
            f'are supported, but you have entered {len(participants)} participants.'
        assert len(participants) >= 4, f"Must have at least 4 participants, but you have {len(participants)}."

        # a bracket will be represented by a binary tree, and will be processed from the "bottom up" as games occur
        n_rounds = int(math.log2(len(participants)))

        # initialize first round of "events", assumes they are in "seeded" order, i.e.,
        # the first 2 participants will play eachother first, the winner of those 2 will play the winner of the next 2 and so on...
        ordered_events = []
        for event_number, i in enumerate(range(0, len(participants), 2)):
            ordered_events.append(Event.first_round_event(
                event_id=event_number+1,  # id events by order in list, 1-indexed
                home_participant=participants[i],
                away_participant=participants[i+1],
            ))

        # a bracket is represented by the root of a binary tree of events
        events_to_process = Bracket.connect_bracket(ordered_events)
        for i in range(len(events_to_process)):
            assert events_to_process[i].event_id == i+1
            
        assert len(events_to_process) == sum([2**n for n in range(n_rounds)])
        bracket_root = events_to_process[-1]  # last event will be the "root" or championship

        super().__init__(
            participants=participants,
            n_rounds=n_rounds,
            n_unique_events=len(events_to_process),
            bracket_root=bracket_root,
        )
        
        
        # construct "private" attributes that pydantic don't need to know 'bout
        # all this stuff has to happen after the pydantic super class is initialized
        self._events_to_process = events_to_process
        self._odds_api_key = odds_api_key

        # dedicated thread for the loop that updates bracket state indefinitely
        self._stop_event = threading.Event()
        self._update_thread = threading.Thread(target=self.process_indefinitely)
        self._update_thread.daemon = True  # Set the thread as daemon so it stops when we interrupt the process

        # Read/write odds from/to disk when available to avoid extra api calls
        # and slow startup time to pre-populate bracket. Will only cache spreads that are
        # final, i.e., games that have at least started alread. Useful when debugging :)
        self._cache_spread = cache_spread
        if cache_spread:
            LOGGER.info("Using spread cache!")
            if os.path.exists(SPREAD_CACHE_FILE):
                LOGGER.info(f"Existing cache file found at: {SPREAD_CACHE_FILE}")
                with open(SPREAD_CACHE_FILE, 'rb') as f:
                    self._matchup_to_spread = pickle.load(f)
            else:
                LOGGER.info(f"No cache file found at: {SPREAD_CACHE_FILE}. Creating one!")
                self._matchup_to_spread = {}
    
    def get_state_metadata(self):
        return {
            'calls_to_epsn': self.calls_to_espn,
            'calls_to_odds_api': self.calls_to_odds_api,
            'is_successfully_updating': self.successfully_updating,
            'last_successful_update': str(self.last_successful_update),
            'last_attempted_update': str(self.last_attempted_update),
            'total_games_in_bracket': self.n_unique_events,
            'total_games_incomplete': len(self._events_to_process),
        }
    

    @property
    def events_in_progress(self):
        return [event for event in self._events_to_process if event.status == 'STATUS_IN_PROGRESS']

    def write_spreads_to_disk(self):
        if self._cache_spread:
            with open(SPREAD_CACHE_FILE, 'wb') as f:
                pickle.dump(self._matchup_to_spread, f)
    
    @staticmethod
    def connect_bracket(initial_ordered_events):
        """
        Assumes initial_ordered_events is the list of games such that the winner of the first two
        events in the list will play each other... and so on.
        """
        events_in_round = initial_ordered_events
        all_events = []
        event_number = len(initial_ordered_events)
        while len(events_in_round) > 1:
            assert len(events_in_round) % 2 == 0  # even number of matches in all rounds except the championship
            next_round_events = []
            all_events += events_in_round  # add to list of all outstanding events
            for i in range(0, len(events_in_round), 2):
                left, right = events_in_round[i], events_in_round[i+1]
                assert left.round == right.round
                # initialize "parent" event in next round and connect to left and right children
                parent_event = Event.from_children(
                    event_id=event_number+1,
                    left=left,
                    right=right
                )
                event_number += 1
                left._parent = parent_event
                right._parent = parent_event
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
        if self._cache_spread and event.matchup_tuple in self._matchup_to_spread:
                spread = self._matchup_to_spread[event.matchup_tuple]
        else:
            # call odds api
            spread = self.get_spread(event, date_str)
        event.spread = spread
            
        # if this event has already began, we can consider this spread final 
        if event.status == 'STATUS_FINAL' or event.status == 'STATUS_IN_PROGRESS':
                event.spread_final = True
        
        # maybe write new spread to cache
        if self._cache_spread:
            if event.matchup_tuple not in self._matchup_to_spread and spread is not None:
            # avoid caching spreads that were not found for some reason, just incase...
                self._matchup_to_spread[event.matchup_tuple] = spread
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
            f'apiKey={self._odds_api_key}&regions=us&markets=spreads&dateFormat=iso&oddsFormat=american&date={date_str}'
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
        determined_events = deque([event for event in self._events_to_process if event.matchup_determined])
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
                if event._parent.matchup_determined:
                    determined_events.append(event._parent)
                if event.is_complete:
                    self._events_to_process.remove(event)
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
            event for event in self._events_to_process if event.is_starting_within_the_hour
            and event.status == 'STATUS_SCHEDULED'
        ]
        return len(self.events_in_progress) > 0 and len(events_starting_soon) > 0
    
    def stop(self):
        self._stop_event.set()
        self._update_thread.join()
    
    def start(self):
        self._update_thread.start()
    
    def process_indefinitely(self):
        while self._events_to_process and not self._stop_event.is_set():
            if True:  # self.should_query(): TODO - add this optimization to significantly reduce api calls once status stuff tested 
                events_to_remove = []
                try:
                    current_event_data_by_matchup_tuple = self.get_score_data(self.current_date_range_str())
                    events_to_remove = []
                    for event in self._events_to_process:
                        # update
                        if event.matchup_determined and event.matchup_tuple in current_event_data_by_matchup_tuple:
                            event_data = current_event_data_by_matchup_tuple[event.matchup_tuple]
                            # FIXME: if the game's available in ESPN much before odds-api, will this result in tons of API calls?
                            # So far it seems like they are available at nearly the same time.
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
                    self._events_to_process.remove(event)

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
    def from_config(cls, config, cache_spread=True):
        odds_api_key = config['ODDS_API_KEY']
        participant_df = pd.read_csv(config['TEAM_CSV_PATH'])
        assert all([key in participant_df.columns for key in REQUIRED_KEYS])
        participants = []
        for i, row in participant_df.iterrows():
            team = Team(
                name=row[BracketCSVColumn.TEAM_NAME_COL.value],
                seed=row[BracketCSVColumn.SEED_COL.value],
                code_name=row[BracketCSVColumn.CODE_NAME_COL.value],
                odds_api_name=row[BracketCSVColumn.ODDS_API_TEAM_NAME_COL.value],
                original_position=i,
            )
            participant = Participant(
                name=row[BracketCSVColumn.PARTICIPANT_NAME_COL.value],
                team=team
            )
            participants.append(participant)
        return cls(participants, odds_api_key, cache_spread=cache_spread)
