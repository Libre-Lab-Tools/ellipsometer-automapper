// XY Stage Controller - Version 2
// Arduino UNO R4 + two A4988 stepper drivers
// Uses the AccelStepper library for acceleration/deceleration.
//
// Design philosophy:
// - The firmware controls one axis at a time.
// - X and Y are tracked internally in motor steps.
// - The physical position at power-up is treated as X = 0, Y = 0.
// - SETORIGIN redefines the current position as X = 0, Y = 0.
// - Position is estimated from commanded steps; there are no encoders/homing sensors.
// - Speed and acceleration are internal firmware parameters, not user commands.
//
// Serial commands:
//   MOVE X 5
//   MOVE Y -2.5
//   MOVEREL X 1
//   MOVEREL Y -0.5
//   SETORIGIN
//   POS?
//   STATUS?
//
// Responses:
//   OK
//   DONE
//   POS 1.000 -2.500
//   STATUS IDLE
//   STATUS MOVING X
//   STATUS MOVING Y
//   ERROR COMMAND
//   ERROR AXIS
//   ERROR VALUE
//   ERROR BUSY

#include <AccelStepper.h>

// ============================================================
// Hardware configuration
// ============================================================

const int stepPinX = 2;
const int dirPinX  = 3;

const int stepPinY = 4;
const int dirPinY  = 5;

// AccelStepper DRIVER mode uses STEP + DIR pins.
AccelStepper stepperX(AccelStepper::DRIVER, stepPinX, dirPinX);
AccelStepper stepperY(AccelStepper::DRIVER, stepPinY, dirPinY);

// Positive direction convention:
//   +X = right
//   +Y = up
//
// false means AccelStepper's normal positive direction drives DIR HIGH.
// X was already tested with DIR HIGH = +X, so keep false.
// If Y moves downward for a positive command, change invertDirectionY to true.
const bool invertDirectionX = false;
const bool invertDirectionY = false;

// Keep separate calibration constants even if both axes currently use the same mechanics.
const float stepsPerMM_X = 40.0;
const float stepsPerMM_Y = 40.0;

// ============================================================
// Motion tuning parameters
// ============================================================
// These are internal parameters, not serial commands.
// Tune experimentally with the stage fully assembled and loaded.
//
// Previous firmware used approximately one step every 6000 us:
//     1 / 0.006 s = ~167 steps/s
// So 160 steps/s starts close to the previously tested speed.
//
// At 40 steps/mm:
//     160 steps/s  = 4.0 mm/s maximum speed
//     300 steps/s² = 7.5 mm/s² acceleration

const float maxSpeed_X     = 160.0;  // steps/second
const float acceleration_X = 300.0;  // steps/second^2

const float maxSpeed_Y     = 160.0;  // steps/second
const float acceleration_Y = 300.0;  // steps/second^2

// A4988 requires only a short STEP pulse. AccelStepper controls the
// interval between pulses according to speed and acceleration.
const unsigned int minStepPulseUs = 2;

// ============================================================
// Controller state
// ============================================================

bool isMoving = false;
char movingAxis = '\0';

// Serial input is collected one line at a time.
String inputLine = "";


void setup() {
  Serial.begin(9600);
  inputLine.reserve(64);

  // Direction configuration.
  stepperX.setPinsInverted(invertDirectionX, false, false);
  stepperY.setPinsInverted(invertDirectionY, false, false);

  // Pulse width for A4988 STEP input.
  stepperX.setMinPulseWidth(minStepPulseUs);
  stepperY.setMinPulseWidth(minStepPulseUs);

  // Internal motion settings.
  stepperX.setMaxSpeed(maxSpeed_X);
  stepperX.setAcceleration(acceleration_X);

  stepperY.setMaxSpeed(maxSpeed_Y);
  stepperY.setAcceleration(acceleration_Y);

  // AccelStepper starts at position 0 by default, but make the
  // startup-origin behavior explicit.
  stepperX.setCurrentPosition(0);
  stepperY.setCurrentPosition(0);
}


void loop() {
  readSerial();
  updateMotion();
}


// ============================================================
// Serial communication
// ============================================================

void readSerial() {
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (inputLine.length() > 0) {
        inputLine.trim();
        inputLine.toUpperCase();
        processCommand(inputLine);
        inputLine = "";
      }
    } else {
      // Prevent an accidentally long input from growing indefinitely.
      if (inputLine.length() < 63) {
        inputLine += c;
      }
    }
  }
}


void processCommand(const String &line) {
  // Queries are allowed even while a motor is moving.
  if (line == "POS?") {
    reportPosition();
    return;
  }

  if (line == "STATUS?") {
    reportStatus();
    return;
  }

  // SETORIGIN changes the internal coordinate system, so do not
  // allow it while a motor is moving.
  if (line == "SETORIGIN") {
    if (isMoving) {
      Serial.println("ERROR BUSY");
      return;
    }

    stepperX.setCurrentPosition(0);
    stepperY.setCurrentPosition(0);
    Serial.println("OK");
    return;
  }

  if (line.startsWith("MOVE ")) {
    handleMoveCommand(line, false);
    return;
  }

  if (line.startsWith("MOVEREL ")) {
    handleMoveCommand(line, true);
    return;
  }

  Serial.println("ERROR COMMAND");
}


void handleMoveCommand(const String &line, bool relativeMove) {
  if (isMoving) {
    Serial.println("ERROR BUSY");
    return;
  }

  // Expected formats:
  //   MOVE X 5
  //   MOVE Y -2.5
  //   MOVEREL X 1
  //   MOVEREL Y -0.5
  int firstSpace = line.indexOf(' ');
  int secondSpace = line.indexOf(' ', firstSpace + 1);

  if (firstSpace < 0 || secondSpace < 0) {
    Serial.println("ERROR COMMAND");
    return;
  }

  String axisText = line.substring(firstSpace + 1, secondSpace);
  String valueText = line.substring(secondSpace + 1);
  axisText.trim();
  valueText.trim();

  if (axisText.length() != 1 || (axisText[0] != 'X' && axisText[0] != 'Y')) {
    Serial.println("ERROR AXIS");
    return;
  }

  float value;
  if (!parseNumber(valueText, value)) {
    Serial.println("ERROR VALUE");
    return;
  }

  char axis = axisText[0];
  long targetSteps;

  if (axis == 'X') {
    long requestedSteps = lround(value * stepsPerMM_X);

    if (relativeMove) {
      targetSteps = stepperX.currentPosition() + requestedSteps;
    } else {
      targetSteps = requestedSteps;
    }
  } else {
    long requestedSteps = lround(value * stepsPerMM_Y);

    if (relativeMove) {
      targetSteps = stepperY.currentPosition() + requestedSteps;
    } else {
      targetSteps = requestedSteps;
    }
  }

  Serial.println("OK");

  // A valid command that requests the current position requires no motion.
  long currentSteps = (axis == 'X') ? stepperX.currentPosition()
                                    : stepperY.currentPosition();

  if (targetSteps == currentSteps) {
    Serial.println("DONE");
    return;
  }

  startMove(axis, targetSteps);
}


bool parseNumber(const String &text, float &value) {
  const char *start = text.c_str();
  char *endPointer;

  double parsed = strtod(start, &endPointer);

  // No numeric characters were found.
  if (endPointer == start) {
    return false;
  }

  // The complete field must be numeric.
  if (*endPointer != '\0') {
    return false;
  }

  value = (float)parsed;
  return true;
}


// ============================================================
// Motion control
// ============================================================

void startMove(char axis, long targetSteps) {
  movingAxis = axis;
  isMoving = true;

  if (axis == 'X') {
    stepperX.moveTo(targetSteps);
  } else {
    stepperY.moveTo(targetSteps);
  }
}


void updateMotion() {
  if (!isMoving) {
    return;
  }

  // run() is non-blocking. It generates a step only when one is due
  // and automatically handles acceleration and deceleration.
  bool stillRunning;

  if (movingAxis == 'X') {
    stillRunning = stepperX.run();
  } else {
    stillRunning = stepperY.run();
  }

  if (!stillRunning) {
    finishMove();
  }
}


void finishMove() {
  isMoving = false;
  movingAxis = '\0';
  Serial.println("DONE");
}


// ============================================================
// Reports
// ============================================================

void reportPosition() {
  float x = stepperX.currentPosition() / stepsPerMM_X;
  float y = stepperY.currentPosition() / stepsPerMM_Y;

  // Fixed machine-readable order: POS X Y
  Serial.print("POS ");
  Serial.print(x, 3);
  Serial.print(" ");
  Serial.println(y, 3);
}


void reportStatus() {
  if (!isMoving) {
    Serial.println("STATUS IDLE");
  } else {
    Serial.print("STATUS MOVING ");
    Serial.println(movingAxis);
  }
}
