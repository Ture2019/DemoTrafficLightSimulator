#!/usr/bin/python3.6 -tt
# -*- coding: utf-8 -*-
# pylint: disable=redefined-outer-name, missing-docstring, invalid-name, global-statement, unused-variable
""" Demo Simulator for a traffic light. """
import os
import random
from collections import namedtuple
from functools import partial, wraps
from time import sleep

import simpy
from pandas import DataFrame, Series

# Global variables
ENV = None
TRAFFIC_LIGHT = None
ANIMATION_STRING = """
Animation:                                                                 ┌───┐                                          
                                                                           │ g │                                          
┌───────┐                                                                  │ y │                                          
│ time  │                                                                  │ r │     .▕       ▕ ─.                        
└───────┘                                                                  └─╦─┘_.──'             `───.                   
─────────────────────────────────────────────────────────────────────────────╝ '       .───────.       `──────────────────
 ───▶                                                                                ,'         `.                        
══════════════════════════════════════════════════════════════════════════════      ───────────────     ══════════════════
                                                                                                                          
─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─                           ─ ─ ─ ─ ─ ─ ─ ─ ─ 
 roadway
═══════════════════════════════════════════════════════c══════════════════p══s       ─────────────      ══════════════════
 ◀───                                                                                 `.       ,'                         
───────────────────────────────────────────────────────────────────────────────`.       `─────'       ,'──────────────────
                                                                                 `───.           _.──'                    
                                                                                      `─────────'                         
"""

class TrafficLight():
    def __init__(self):
        self.signal = 'red'
        self.most_recent_call_time = -1
        self.light_cycle = None
        self.turns_green = ENV.event()
        self.transit_area = simpy.Container(ENV, init=0, capacity=simpy.core.Infinity)
        self.red_light_queue = simpy.Store(ENV, capacity=simpy.core.Infinity)
        self.head_of_queue = {}  # dictionary: bus - event
        self.position_in_queue = {}  # dictionary: bus - int

    def run_traffic_light_cycle(self):
        try:
            start = ENV.now

            self.signal = 'red'
            yield ENV.timeout(10)

            self.signal = 'yellow1'
            yield ENV.timeout(1)

            self.signal = 'green'
            self.turns_green.succeed()
            yield ENV.timeout(40)

            if self.most_recent_call_time > start:
                #Prolong light cycle
                yield ENV.timeout(20)

        except simpy.Interrupt:
            #Shorten light cycle
            pass

        self.signal = 'yellow2'
        self.turns_green = ENV.event()

        #Run uninterupted:
        yellow2_time = 3
        while yellow2_time > 0.1:
            try:
                yellow2_started = ENV.now
                yield ENV.timeout(yellow2_time)
            except simpy.Interrupt:
                pass
            finally:
                yellow2_time = yellow2_time - (ENV.now - yellow2_started)
        self.signal = 'red'

        if self.transit_area.level >= 1:
            # Restart light cycle if there are buses in transit area
            self.light_cycle = ENV.process(self.run_traffic_light_cycle())
        return

    def trigger_call_detector(self):
        self.transit_area.put(1)
        self.most_recent_call_time = ENV.now
        #if not self.light_cycle or self.light_cycle.processed:
        #    #print('Call')
        #    self.light_cycle = ENV.process(self.run_traffic_light_cycle())

    def trigger_proximity_sensor(self):
        if (not self.light_cycle or self.light_cycle.processed):
            #print('Proximity')
            self.light_cycle = ENV.process(self.run_traffic_light_cycle())

    def trigger_sign_off_detector(self):
        self.transit_area.get(1)
        if self.light_cycle and not self.light_cycle.processed and self.transit_area.level == 0:
            #print('Sign-off')
            self.light_cycle.interrupt()

    def enqueue(self, bus):
        self.head_of_queue[bus] = ENV.event()
        self.red_light_queue.put(bus)
        pos = self.red_light_queue.items.index(bus)
        self.position_in_queue[bus] = pos
        if pos == 0:
            self.head_of_queue[bus].succeed()
        return self.head_of_queue[bus]

    def dequeue(self, bus):
        self.red_light_queue.get()
        del self.head_of_queue[bus]
        if self.red_light_queue.items:
            next_bus = self.red_light_queue.items[0]
            self.head_of_queue[next_bus].succeed()
        return self.position_in_queue[bus]


Movement = namedtuple('Movement', ['from_time', 'from_pos', 'to_pos', 'speed'])

class Bus():
    def __init__(self, nr: int):
        self.nr = nr
        self.movement = Movement(ENV.now, 0, 0, 0)

    def __str__(self):
        return 'bus%d' % self.nr

    def get_pos(self):
        pos = min(self.movement.from_pos + (ENV.now - self.movement.from_time) * self.movement.speed, self.movement.to_pos)
        return pos

    def get_time_for_movement(self):
        return (self.movement.to_pos - self.movement.from_pos) / self.movement.speed

    def drive(self):
        #Drive to call detector
        self.movement = Movement(ENV.now, 0, 260, 13)
        yield ENV.timeout(self.get_time_for_movement())
        TRAFFIC_LIGHT.trigger_call_detector()

        #Drive to decision point
        self.movement = Movement(ENV.now, 260, 310, 13)
        yield ENV.timeout(self.get_time_for_movement())

        if TRAFFIC_LIGHT.signal == 'red' or TRAFFIC_LIGHT.red_light_queue.items:
            #Drive to proximity sensor
            self.movement = Movement(ENV.now, 310, 365, 6.5)
            yield ENV.timeout(self.get_time_for_movement())
            TRAFFIC_LIGHT.trigger_proximity_sensor()

            #Drive to stop line
            self.movement = Movement(ENV.now, 365, 380, 6.5)
            yield ENV.timeout(self.get_time_for_movement())

            #Wait for first place in queue
            yield TRAFFIC_LIGHT.enqueue(self)

            #Wait for green
            yield TRAFFIC_LIGHT.turns_green
            yield ENV.timeout(1) #driver reaction time

            #Drive out of queue
            TRAFFIC_LIGHT.dequeue(self)
            TRAFFIC_LIGHT.trigger_sign_off_detector()

            #Accelerate to cruise speed
            self.movement = Movement(ENV.now, 380, 430, 6.5)
            yield ENV.timeout(self.get_time_for_movement())

        else:
            #Drive to proximity sensor
            self.movement = Movement(ENV.now, 310, 365, 13)
            yield ENV.timeout(self.get_time_for_movement())
            TRAFFIC_LIGHT.trigger_proximity_sensor()

            #Drive to stop line
            self.movement = Movement(ENV.now, 365, 380, 13)
            ENV.timeout(self.get_time_for_movement())
            TRAFFIC_LIGHT.trigger_sign_off_detector()

            #Drive over roundabout
            self.movement = Movement(ENV.now, 380, 430, 13)
            yield ENV.timeout(self.get_time_for_movement())

        #Drive rest of the track
        self.movement = Movement(ENV.now, 430, 600, 13)
        yield ENV.timeout(self.get_time_for_movement())


def patch_bus(bus, monitor):
    """ Patch *bus* so it can be monitored. For reporting."""
    def get_wrapper(func):
        #Generate a wrapper for drive
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = ENV.now
            process = ENV.process(func(*args, **kwargs))
            yield process # wait until finished
            duration = ENV.now - start
            monitor({'bus': bus.nr, 'travel_time': duration})
            return process.value
        return wrapper

    setattr(bus, 'drive', get_wrapper(getattr(bus, 'drive')))

def create_constant_stream_of_buses(fleet, observations, mean_time_between_buses=10):
    def monitor(observations, observation):
        observations.append(observation)
    monitor = partial(monitor, observations)

    bus_counter = 0
    while True:
        bus_counter += 1
        bus = Bus(nr=bus_counter)
        patch_bus(bus, monitor)
        fleet.append(bus)
        ENV.process(bus.drive())
        random_duration = random.expovariate(1 / mean_time_between_buses)
        yield ENV.timeout(random_duration)

def set_stage():
    global ENV, TRAFFIC_LIGHT
    ENV = simpy.Environment()
    TRAFFIC_LIGHT = TrafficLight()
    fleet, observations = [], []
    ENV.process(create_constant_stream_of_buses(fleet, observations))
    return fleet, observations

def main():    
    print('Reporting:')
    fleet, observations = set_stage()
    ENV.run(until=100)
    print('Fleet size:', len(fleet))
    report = DataFrame(data=observations)
    print('Avg. travel time: %.1f' % report.travel_time.mean())
    sleep(3)

    #Unit testing:

    #Optimization:

    #Animation:
    fleet, observations = set_stage()
    last = 0
    for i in range(1, 100):
        os.system('clear')
        if TRAFFIC_LIGHT.signal == 'green':
            g, y, r = '◍', '○', '○'
        elif TRAFFIC_LIGHT.signal in ['yellow1', 'yellow2']:
            g, y, r = '○', '◍', '○'
        else:
            g, y, r = '○', '○', '◍'
        roadway = list(' ' * 121)
        data = [int(round(bus.get_pos() / 600 * 120, 0)) for bus in fleet]
        positions = Series(data=data, name='frequence').value_counts().reset_index().sort_values('index').values.tolist()
        for position in positions:
            if position[1] == 1:
                replacement = '🀰'
            else:
                replacement = str(position[1])
            roadway[position[0]] = replacement
        roadway = ''.join(roadway)
        time = ('%d' % ENV.now).rjust(4, ' ')
        rep = ANIMATION_STRING.replace('roadway', roadway).replace('time', time).replace('g', g).replace('y', y).replace('r', r)
        print(rep)
        ENV.run(until=i)
        sleep(.5)

if __name__ == "__main__":
    os.system('clear')
    main()