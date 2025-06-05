# obci_brainflow_lsl.py
'''
Author: Thiago Rossi Roque @thiago-roque07
Hyper OpenBCI - Use BrainFlow to read data from two OpenBCI boards simultaneously and send it as LSL streams.

This program is based on scripts originally created by @retiutut and @marles77.
https://github.com/OpenBCI/OpenBCI_GUI/tree/master/Networking-Test-Kit/LSL
https://github.com/marles77/openbci-brainflow-lsl

Install dependencies with:
pip install --upgrade numpy brainflow pylsl pyserial PyYAML 

Use:
python obci_brainflow_lsl_duo.py --set cyton_1.yml cyton_2.yml
'''

import yaml
import os
import sys
import time
import numpy as np
import pandas as pd
import threading
import brainflow
from brainflow.board_shim import BoardShim, BrainFlowInputParams
from pylsl import StreamInfo, StreamOutlet, local_clock

if sys.platform.lower() == "win32": 
    os.system('color')
    
# ==== constants ====

CRED = '\033[91m'
CGREEN  = '\33[32m'
CYELLOW = '\33[33m'
CEND = '\033[0m'
# default openbci channel commands (to avoid reflushing the board)
OBCI_COMMANDS = ("x1060110X", "x2060110X", "x3060110X", "x4060110X", # channels 1-4   /Cyton
                 "x5060110X", "x6060110X", "x7060110X", "x8060110X", # channels 5-8   /Cyton
                 "xQ060110X", "xW060110X", "xE060110X", "xR060110X", # channels 9-12  /Daisy
                 "xT060110X", "xY060110X", "xU060110X", "xI060110X") # channels 13-16 /Daisy

BOARD_N_CHANNELS = {0: 8, 0: 8, 5: 8, 2: 16, 6: 16} # Cyton or Cyton + Daisy
ALLOWED_DATA_TYPES = ['EEG', 'stim']
REQUIRED_ARGS = ("board_id", "name", "data_type", "channel_names", "uid", "max_time")
REQUIRED_ARDUINO = ("name", "type", "channel_count", "channel_format", "source_id")
MARKER = {'1': 11, '2': 22} # mapping of external markers; can add more but avoid '0' and '7'

# ==== auxiliary functions ====

def channel_select(board, board_id, data_type): 
    # keys in switcher must correspond to ALLOWED_DATA_TYPES constant
    switcher = { 
        'EEG': board.get_exg_channels(board_id),
        'stim': board.get_analog_channels(board_id)
    }   
    return switcher.get(data_type, "error")


def read_settings(file_name):
        '''Reads args and board commands from settings file'''

        with open(file_name) as file:
            data = yaml.safe_load(file);

        if data:
            print(CGREEN + "Settings read successfully" + CEND);
            return data
        else:
            print(CRED + "Failed to read settings form file" + CEND);
            return None


def user_choice(prompt, boards = None, thread_initiated = False):
    '''Awaits user's choice (yes or quit). 
    This function can be used to give the user some control'''
    
    user_res = ''
    while True:
        user_res = input(CYELLOW + prompt + CEND)
        if (user_res == 'y') and (not thread_initiated) and (not stop_event.is_set()):
            break
        if user_res == 'q':
            if thread_initiated:
                stop_event.set() # message for threads to stop
                time.sleep(1)
            if boards:
                try:
                    for board in boards:
                        board.stop_stream()
                except brainflow.board_shim.BrainFlowError:
                    print(CRED + "Board is not streaming" + CEND)
                for board in boards:
                    board.release_session()
            print('The end')
            time.sleep(1)
            sys.exit()
        else:
            continue


def default_chan_commands(board_id, chan_commands = None):
    '''Creates a dictionary of default commands for specified board'''

    if not chan_commands:
        n_channels = BOARD_N_CHANNELS[board_id]
        chan_commands = {'chan' + str(num + 1): OBCI_COMMANDS[num] for num in range(0, n_channels)}
        return chan_commands


def manage_settings_data(data):
    '''Reads and manages settings data'''

    args = data.get("args", None)
    chan_commands = data.get("commands", None)
    if args:
        # manage missing required args
        
        missing_args = [p for p in REQUIRED_ARGS if args.get(p) == None]
        if missing_args:
            print(CRED + "Missing args:" + CEND, "\n", ", ".join(missing_args), "\nThe end", sep='')
            sys.exit()
        if args['board_id'] not in BOARD_N_CHANNELS:
            print(CRED + "Unsupported board" + CEND, "\nThe end", sep='')
            sys.exit()
        # allowed data types check
        if not all(type in ALLOWED_DATA_TYPES for type in args['data_type']):
            print(CRED + f"Not allowed data type/s in settings. Allowed data types: "
                  + f"{', '.join(ALLOWED_DATA_TYPES)}" + CEND + "\nThe end")
            sys.exit()
        # yaml does not support empty strings, so None values have to be converted
        args['ip_address'] = args['ip_address'] if args.get('ip_address') else ""
        args['ip_port'] = args['ip_port'] if args.get('ip_port') else ""
        args['streamer_params'] = args['streamer_params'] if args.get('streamer_params') else ""
        args['serial_port'] = args['serial_port'] if args.get('serial_port') else ""
        args['master_id'] = args['master_id'] if args.get('master_id') else "2" 
    else:
        print(CRED + "No args" + CEND + "\nThe end")
        sys.exit()

    if not chan_commands:
        # setting default chan commands for Cyton (+ Daisy); alternatively command 'd' can be sent to board
        print(CYELLOW + "No commands. Using default." + CEND, "\nThe end", sep='')
        chan_commands = default_chan_commands(args['board_id'])
    
    return args, chan_commands


def collect_cont(board, args, srate, outlet, fw_delay):
    '''Collects continuous data with brainflow and sends it via LSL'''
    
    chans = []
    sent_samples = 0
    data = []
    mychunk = []

    # Combine channels for all data types
    for type in args['data_type']:
        chans.extend(channel_select(board=board, board_id=args['board_id'], data_type=type))
    
    print(CGREEN + "Now sending data from board..." + CEND)
    start_time = local_clock()

    while not stop_event.is_set():
        # Continuous data collection
        elapsed_time = local_clock() - start_time
        data_from_board = board.get_board_data()

        required_samples = int(srate * elapsed_time) - sent_samples
        if required_samples > 0:
            data = data_from_board[chans]
            mychunk = []
            for i in range(len(data[0])):
                mychunk.append(data[:, i].tolist())
            stamp = local_clock() - fw_delay
            outlet.push_chunk(mychunk, stamp)
            sent_samples += required_samples

        if elapsed_time > args['max_time']:
            stop_event.set()  # Message for threads to stop
            print(CRED + "\nTime limit reached! Data collection has been stopped." + CEND
                  + CYELLOW + "\nPress 'q' + ENTER to exit\n--> " + CEND, end='')

        time.sleep(1)




# ==== main function ====
def main(argv):
    '''Takes args and initiates streaming''' 

    if argv:
        # manage settings read form a yaml file
        if argv[0] == '--set':
            file_settings_OBCI_1 = argv[1]
            data_OBCI_1 = read_settings(file_settings_OBCI_1)
            if not data_OBCI_1:
                print('The end')
                sys.exit()
            args_OBCI_1, chan_commands_OBCI_1 = manage_settings_data(data_OBCI_1)
            
            file_settings_OBCI_2 = argv[2]
            data_OBCI_2 = read_settings(file_settings_OBCI_2)
            if not data_OBCI_2:
                print('The end')
                sys.exit()
            args_OBCI_2, chan_commands_OBCI_2 = manage_settings_data(data_OBCI_2)
            
    else:
        print(CRED + "Use --set *.yml to load settings" + CEND, "\nThe end", sep='')
        sys.exit()

    ser = None

    user_choice("Initiate? 'y' -> yes, 'q' -> quit\n--> ")

    BoardShim.enable_dev_board_logger()

    # brainflow initialization
    params_OBCI_1 = BrainFlowInputParams()
    params_OBCI_1.serial_port = args_OBCI_1['serial_port']
    params_OBCI_1.ip_address = args_OBCI_1['ip_address']
    board_OBCI_1 = BoardShim(args_OBCI_1['board_id'], params_OBCI_1)
    
    params_OBCI_2 = BrainFlowInputParams()
    params_OBCI_2.serial_port = args_OBCI_2['serial_port']
    params_OBCI_2.ip_address = args_OBCI_2['ip_address']
    board_OBCI_2 = BoardShim(args_OBCI_2['board_id'], params_OBCI_2)

    # LSL internal outlet (stream from board) configuration and initialization 
    # Combine channel names and count total channels
    channel_names_OBCI_1 = []
    n_channels_OBCI_1 = 0
    for type in args_OBCI_1['data_type']:
        channel_names_OBCI_1.extend(args_OBCI_1['channel_names'][type].split(','))
        n_channels_OBCI_1 += len(args_OBCI_1['channel_names'][type].split(','))

    srate_OBCI_1 = board_OBCI_1.get_sampling_rate(args_OBCI_1['board_id'])
    name_OBCI_1 = args_OBCI_1['name'] + "_" + args_OBCI_1['serial_port']
    uid = args_OBCI_1['uid'] + "_" + args_OBCI_1['serial_port']

    info_OBCI_1 = StreamInfo(name_OBCI_1, 'EEG_AUX', n_channels_OBCI_1, srate_OBCI_1, 'double64', uid)

    # Add channel labels and types
    chans_OBCI_1 = info_OBCI_1.desc().append_child("channels")
    for type in args_OBCI_1['data_type']:
        for label in args_OBCI_1['channel_names'][type].split(','):
            chan_OBCI_1 = chans_OBCI_1.append_child("channel")
            chan_OBCI_1.append_child_value("label", label)
            chan_OBCI_1.append_child_value("type", type)

    outlet_int_OBCI_1 = StreamOutlet(info_OBCI_1)
    fw_delay_OBCI_1 = args_OBCI_1['delay']

    # Prepare session; exit if board is not ready
    try:
        board_OBCI_1.prepare_session()
    except brainflow.board_shim.BrainFlowError as e:
        print(CRED + f"Error: {e}" + CEND)
        print("The end")
        time.sleep(1)
        sys.exit()


    # LSL internal outlet (stream from board) configuration and initialization 
    # Combine channel names and count total channels
    channel_names_OBCI_2 = []
    n_channels_OBCI_2 = 0
    for type in args_OBCI_2['data_type']:
        channel_names_OBCI_2.extend(args_OBCI_2['channel_names'][type].split(','))
        n_channels_OBCI_2 += len(args_OBCI_2['channel_names'][type].split(','))

    srate_OBCI_2 = board_OBCI_2.get_sampling_rate(args_OBCI_2['board_id'])
    name_OBCI_2 = args_OBCI_2['name'] + "_" + args_OBCI_2['serial_port']
    uid = args_OBCI_2['uid'] + "_" + args_OBCI_2['serial_port']

    info_OBCI_2 = StreamInfo(name_OBCI_2, 'EEG_AUX', n_channels_OBCI_1, srate_OBCI_2, 'double64', uid)

    # Add channel labels and types
    chans_OBCI_2 = info_OBCI_2.desc().append_child("channels")
    for type in args_OBCI_2['data_type']:
        for label in args_OBCI_2['channel_names'][type].split(','):
            chan_OBCI_2 = chans_OBCI_2.append_child("channel")
            chan_OBCI_2.append_child_value("label", label)
            chan_OBCI_2.append_child_value("type", type)

    outlet_int_OBCI_2 = StreamOutlet(info_OBCI_2)
    fw_delay_OBCI_2 = args_OBCI_2['delay']

    # Prepare session; exit if board is not ready
    try:
        board_OBCI_2.prepare_session()
    except brainflow.board_shim.BrainFlowError as e:
        print(CRED + f"Error: {e}" + CEND)
        print("The end")
        time.sleep(1)
        sys.exit()

    # iterate over channel commands, send one and wait for a response from board
    # to restore default channel settings 'd' can be sent
    print("Configuring Board 1:")
    # print(chan_commands_OBCI_1)
    for chan, command in chan_commands_OBCI_1.items():
        res_string = board_OBCI_1.config_board(command)
        time.sleep(0.1)
        if res_string.find('Success') != -1:
            res = CGREEN + res_string + CEND
        else:
            res = CRED + res_string + CEND
        print(f"Response from {chan}: {res}")
    
        
    print("Configuring Board 2:")
    print(chan_commands_OBCI_2)
    for chan, command in chan_commands_OBCI_2.items():
        res_string = board_OBCI_2.config_board(command)
        time.sleep(0.1)
        if res_string.find('Success') != -1:
            res = CGREEN + res_string + CEND
        else:
            res = CRED + res_string + CEND
        print(f"Response from {chan}: {res}")
        
    # show stream configuration and wait until user accepts or quits
    print(50 * "-")
            
    user_choice("Start streaming? 'y' -> yes, 'q' -> quit\n--> ", boards = [board_OBCI_1, board_OBCI_2])
    
    # board starts streaming
    board_OBCI_1.start_stream(45000, args_OBCI_1['streamer_params'])
    
    board_OBCI_2.start_stream(45000, args_OBCI_2['streamer_params'])
    time.sleep(1)
        
    # define threads which will collect continuous data (e.g. EEG) and markers (if arduino/serial is set up)
    thread_cont_OBCI_1 = threading.Thread(target = collect_cont, args = [board_OBCI_1, args_OBCI_1, srate_OBCI_1, outlet_int_OBCI_1, fw_delay_OBCI_1])
    thread_cont_OBCI_1.start()

    thread_cont_OBCI_2 = threading.Thread(target = collect_cont, args = [board_OBCI_2, args_OBCI_2, srate_OBCI_2, outlet_int_OBCI_2, fw_delay_OBCI_2])
    thread_cont_OBCI_2.start()
        
    # wait for stop message from user while running data collecting threads
    time.sleep(2)
    user_choice("To stop streaming and quit press 'q' + ENTER\n--> ", boards = [board_OBCI_1, board_OBCI_2], thread_initiated = True)


# ==== start the program ====
if __name__ == "__main__":
    stop_event = threading.Event()
    main(sys.argv[1:])