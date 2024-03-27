import time
import math

import argparse
import pandas as pd
from collections import deque

from datetime import datetime, timedelta
from enum import Enum

import ipdb
import requests


class Team:

    def __init__(self, name, seed, code_name, original_position):
        self.name = name
        self.seed = seed
        self.code_name = code_name
        self.original_position = original_position


class Participant:

    def __init__(self, name, team):
        self.name = name
        self.team = team
        self.is_in = True
        self.history = []

    def set_team(self, name):
        self.name = name

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
        self.current_spread = None
        self.opening_spread = None
        # espn game info: events can have 1 of 4 states:
        # ['STATUS_SCHEDULED', 'STATUS_FINAL', 'STATUS_IN_PROGRESS', and None if it is TBD]
        self.status = None
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
        # Get the current time
        current_time = datetime.now()

        # Calculate the difference between the current time and the event start time
        time_difference = self.estimated_start_time - current_time

        # Check if the difference is less than or equal to 1 hour
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
            if self.home_participant.team.code_name == winning_team_code:
                self.winning_participant = self.home_participant
            else:
                self.winning_participant = self.away_participant
            self.winning_team = self.winning_participant.team
            self.parent.update_from_child(self)
        
    def update_from_child(self, child):
        assert child.winning_participant is not None
        # update either the left or right child depending on which one was provided upon completion
        if child is self.left:
            self.home_participant = child.winning_participant
        else:
            assert child is self.right
            self.away_participant = child.winning_participant

    def to_str(self):
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
        return f'Event #: {self.event_id}\tRound: {self.round}' +\
            f'\tHome: {home_str}\tAway: {away_str}\tScore: {home_score} - {away_score}'


class Bracket:

    class BracketCSVColumn(Enum):
        PARTICIPANT_NAME_COL = 'participant_name'
        TEAM_NAME_COL = 'team_name'
        SEED_COL = 'seed'
        CODE_NAME_COL = 'team_code'

    REQUIRED_KEYS = [key.value for key in BracketCSVColumn]

    def __init__(self, participants):
        # validate bracket -- currently only works with "even" brackets (e.g., 2, 4, 8, ... participants)
        assert math.log2(len(participants)).is_integer(), f'Only "evenly sized brackets (those with 4, 8, 16 ... 2^n participants.) ' +\
            f'are supported, but you have entered {len(participants)} participants.'
        assert len(participants) >= 4, f"Must have at least 4 participants, but you have {len(participants)}."
        self.participants = participants

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

    def events_in_progress(self):
        events_in_progress = []
        for event in self.events_to_process:
            if event.is_scheduled:
                # must be a "resolved" matchup rather than one TBD in the future
                if event.has_started:
                    events_in_progress.append(event)
        return events_in_progress

    def update_ongoing_events(self, events_in_progress):
        # call scores API
        # update scores
        # check if complete and mark finished
        # update final score, winning team, winning participant
        # update parent node by setting its left/right
        # optionally update the winning participant's team
        # remove from self.events_to_process
        pass

    def check_if_events_have_started(self):
        events_soon = []
        for event in self.events_to_process:
            if event.is_scheduled:
                # must be a "resolved" matchup rather than one TBD in the future
                if event.is_starting_within_the_hour:
                    events_soon.append(event)
        # call scores API
        api_result = dict()  # TODO
        for event in events_soon:
            home_team = event.home_participant.team.name
            if home_team in api_result:
                if api_result[home_team]['state'] == 'in_progress':
                    event.has_started = True
                    # update score 

    
    def pre_populate_events(self):
        # assumes this is run in march...
        year = datetime.now().year
        data_from_march = requests.get(
            f'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={year}03'
        ).json()
        # pre-process this data to make it query-able via two team codes
        event_data_by_matchup_tuple = {}
        for event_data in data_from_march['events']:
            matchup_tuple = tuple(sorted(event_data['shortName'].split(' VS ')))
            if matchup_tuple == ('TBD', 'TBD'):
                continue
            assert len(matchup_tuple) == 2
            assert matchup_tuple not in event_data_by_matchup_tuple  # teams only play once in march or the tournament
            event_data_by_matchup_tuple[matchup_tuple] = event_data
        determined_events = deque([event for event in self.events_to_process if event.matchup_determined])
        # events will move to whatever state exists in the api. We will add more determined events to this!
        while len(determined_events) > 0:
            event = determined_events.popleft()
            matchup_tuple = event.matchup_tuple
            if matchup_tuple in event_data_by_matchup_tuple:
                event.update(event_data_by_matchup_tuple[matchup_tuple])
                # the above update function will update the parent once games are over,
                # so let's check if the next round's game is determined and add it to the queue if so
                if event.parent.matchup_determined:
                    determined_events.append(event.parent)
        return

    
    def process_indefinitely(self):
        while self.events_to_process:
            # if there are any games that have started, query for all scores every ~1 minute
            events_in_progress = self.events_in_progress()
            if len(events_in_progress) > 0:
                self.update_ongoing_events(events_in_progress)
                # TODO - update events in progress based on the api result from the above, in case we missed any?
                time.sleep(60)  # sleep for 1 minutes before checking for score updates again
            # otherwise, let's check if we should move any games to "in progress"
            else:
                self.check_if_events_have_started()  # is this where we should check for opening lines?
                time.sleep(5 * 60)  # make this a much slower loop (5 mins) 

            for event in self.events_to_process():
                # 
                if event.matchup_determined:
                    event.process()
                    # event.check_score()
                    # if event.is_complete()
                    #     event.update_winner()
                    if event.is_complete():
                        pass  # TODO remove from list


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
        return all_events + events_in_round

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
    
    def pretty_print(self):
        queue = deque()
        queue.append(self.bracket_root)
        current_round = float('inf')
        while queue:
            event = queue.popleft()
            if event.round < current_round:
                print('\n\n')
                print('=' * 50 + self.round_description(event.round) + '=' * 50)
                current_round = event.round
            print(event.to_str())
            if event.left:
                queue.append(event.left)
            if event.right:
                queue.append(event.right)

    @classmethod
    def from_csv(cls, team_csv):
        participant_df = pd.read_csv(team_csv)
        assert all([key in participant_df.columns for key in cls.REQUIRED_KEYS])
        participants = []
        for i, row in participant_df.iterrows():
            team = Team(
                row[cls.BracketCSVColumn.TEAM_NAME_COL.value],
                row[cls.BracketCSVColumn.SEED_COL.value],
                row[cls.BracketCSVColumn.CODE_NAME_COL.value],
                original_position=i,
            )
            participant = Participant(row[cls.BracketCSVColumn.PARTICIPANT_NAME_COL.value], team)
            participants.append(participant)
        return cls(participants)





if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Construct bracket from CSV file')
    parser.add_argument('input_teams', help=f'Path to a CSV file with (at least) these columns: {Bracket.REQUIRED_KEYS}')
    args = parser.parse_args()

    bracket = Bracket.from_csv(args.input_teams)
    bracket.pretty_print()
    
    # pre-populate to fill games that have already happened
    bracket.pre_populate_events()
    print(f'\nWith events populated:')
    bracket.pretty_print()

    # for debugging
    import ipdb
    participant_df = pd.read_csv(args.input_teams)
    ipdb.set_trace()

    # while True:
        # update bracket
        # pass
