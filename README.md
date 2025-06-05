# HyperOpenBCI-LSL
Multi-device OpenBCI-LSL bridge based on Brainflow for hyperscanning research

---
Uses Python, BrainFlow, and pylsl to read data from two OpenBCI boards simultaneously and send it as multiple Lab Streaming Layer (LSL) streams.

## Dependencies

```pip install --upgrade numpy brainflow pylsl pyserial PyYAML``` 

## Usage

1. Set both .yml configuration files according to your needs.
The default configuration is for the Cyton+Daisy device.
Be mindful of making shure the COM ports are corrected

2. Run the script according to the example ```python obci_brainflow_lsl-duo.py --set cyton_1.yml cyton_2.yml```

3. Capture the LSL streams using some LSL visualizer or Lab Recorder (https://github.com/labstreaminglayer/App-LabRecorder)


## Limitations and known issues
The script has been tested only on Windows with Cyton+Daisy
This script is ment specifically for capturing two boards simultaneously, with a synchronization precison of ~10ms. If you only need one board at  time, I recommend checking the following projects:
- https://github.com/OpenBCI/OpenBCI_GUI/tree/master/Networking-Test-Kit/LSL
- https://github.com/marles77/openbci-brainflow-lsl

## Acknowledgments
This program is based on a script originally created by [@marles77](https://github.com/marles77/openbci-brainflow-lsl) and [@retiutut](https://github.com/OpenBCI/OpenBCI_GUI/tree/master/Networking-Test-Kit/LSL).