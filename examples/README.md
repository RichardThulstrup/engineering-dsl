# Example worksheets

Sheets made in the [worksheet](../docs/WORKSHEET.md) from its two
templates (*File → New from template…*). Open one with

```bash
python -m utils.worksheet examples/power/system.ipynb
```

A sheet that has not been calculated on your computer shows what it
was saved with, and a bar offering *Calculate*.

## A technical note

[`technical_note_regulator.ipynb`](technical_note_regulator.ipynb):
does the linear regulator that makes the sensor board's 3.3 V stay
cool enough inside the enclosure? Purpose, references, assumptions,
the calculation, the result and the conclusion.

## Power requirements, three levels deep

One sheet for each unit in [`power/`](power/). Each lists its own
loads and hands its total on, under the unit's name, to the sheet of
the unit it is part of:

```
sensor_board ─┐
              ├─ controller ─┐
radio_module ─┘              ├─ system
motor_driver ────────────────┘
```

- **sensor_board**, **radio_module** — on the controller's 5 V rail,
  each with a regulator of its own;
- **motor_driver** — two motors on the 24 V bus;
- **controller** — takes the sensor board and the radio module, adds
  its own processor and display, checks the load on its 5 V converter,
  and hands on what it draws from 24 V;
- **system** — takes the controller and the motor driver, adds a fan,
  and sizes the 24 V power supply.

Each level keeps its own design margin on its own loads only.

To see the chain at work, change a load on the sensor board — say
`P_sensors` — and save it. Opening the system then says *controller is
behind*; *Edit → Bring values up to date…* brings the controller along
and gives the system what it now comes to.
