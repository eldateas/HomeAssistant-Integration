#ifndef RxModule_ /* Include guard */
#define RxModule_

/*
 *  Header-Datei zu RxModule - © ELDAT EaS GmbH 2024, L.Koepping
 *
 *  Filename:       RxModule.c
 *  Author:         L.Koepping
 *  Revised:        01.03.2024
 *  Revision:       v1.0.0
 *
 *  Copyright © 2024 by © ELDAT EaS GmbH, All Rights Reserved.
 *  Permission to use, reproduce, copy, prepare derivative works,
 *  modify, distribute, perform, display or sell this software and/or
 *  its documentation for any purpose is prohibited without the express
 *  written consent of ELDAT EaS GmbH.
 */

#include <stdbool.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>

#if defined(_WIN32) || defined(_WIN64)
#include <windows.h> // windows api serial port functions
#else
#include <termios.h> // termios struct used to define serial connection
#include <fcntl.h> 
#endif

#ifdef RxModule_TEST
#include "RxModule_testExtension.h" // test extension for RxModule
#endif

#define MAX_REQUEST_COUNT 8   // the maximum simultaneous request count
#define MAX_REQUEST_QUEUED 12 // the maximum requests held queued

/////////////////////////////////////// hex codes for different actions ///////////////////////////////////////

static const uint8_t EMPTY_TYPE = 0x00;         // const expression for empty device type
static const uint8_t EWB_DT_BIDI_TR = 0x01;     // generic Easywave Bidi transmitter
static const uint8_t EWB_DT_SWITCH = 0x03;      // switch (on/off)
static const uint8_t EWB_DT_DIMMER = 0x04;      // dimmer
static const uint8_t EWB_DT_MOTOR = 0x05;       // motor control
static const uint8_t EWB_DT_DUAL_SWITCH = 0x06; // dual switch (on/off)
static const uint8_t EWB_DT_QUAD_SWITCH = 0x07; // quadruple switch (on/off)
static const uint8_t EWB_DT_DUAL_MOTOR = 0x08;  // dual motor control
static const uint8_t EWB_DT_QUAD_MOTOR = 0x09;  // quadruple motor control
static const uint8_t EWB_DT_PART_SWITCH = 0x0A; // part of a dual/quadruple switch
static const uint8_t EWB_DT_PART_MOTOR = 0x0B;  // part of a dual/quadruple motor

static const uint8_t EW_RECEIVER = 0x10;
static const uint8_t EW_TRANSMITTER = 0x11;
static const uint8_t EW_TRANSMITTER_PART = 0x12;
static const uint8_t EW_SENSOR = 0x13;
static const uint8_t EW_SENSOR_PART = 0x14;

static const uint8_t SEC_RECEIVER = 0x21;
static const uint8_t SEC_TRANSMITTER = 0x22;

#define TM_IT_EASW_RELEASE 0x00  // Easywave transmitter; button release
#define TM_IT_EASW_PUSH 0x01     // Easywave transmitter; button push and hold
static const uint8_t TM_IT_SENSOR_DATA = 0x02;   // Sensor data message
static const uint8_t TM_IT_EWBIDI_STATE = 0x03;  // Easywave Bidi receiver state change
static const uint8_t TM_IT_EWBIDI_ABORT = 0x40;  // Easywave Bidi notification about an aborted learn or removal
static const uint8_t TM_IT_EWBIDI_ADD_TR = 0x41; // Easywave Bidi notification about a learned transmitter
static const uint8_t TM_IT_EWBIDI_RMV_TR = 0x42; // Easywave Bidi notification about a removed transmitter
static const uint8_t TM_IT_EWBIDI_LN_T = 0xF0;   // Easywave Bidi learn ack for transmitter
static const uint8_t TM_IT_EWBIDI_CHG_T = 0xF1;  // Easywave Bidi receiver change state for transmitters
static const uint8_t TM_IT_EWBIDI_QUR_T = 0xF2;  // Easywave Bidi receiver query state for transmitters

static const uint8_t TM_BUTTON_MASK = 3; // mask for the button value
static const uint8_t TM_BUTTON_A = 0;    // button A
static const uint8_t TM_BUTTON_B = 1;    // button B
static const uint8_t TM_BUTTON_C = 2;    // button C
static const uint8_t TM_BUTTON_D = 3;    // button D

static const uint8_t TM_BUTTON_FUNC_MASK = 0xFC; // mask for the function of the button
static const uint8_t TM_BUTTON_DEFAULT = 0x00;   // default function of the button
static const uint8_t TM_BUTTON_LRN_DEL = 0x04;   // remote learn; delete transmitter
static const uint8_t TM_BUTTON_LRN_ADD = 0x08;   // remote learn; add transmitter
static const uint8_t TM_BUTTON_LRN_RESET = 0x0C; // remote learn; reset receiver
static const uint8_t TM_BUTTON_LRN_TIMER = 0x10; // remote learn; set the timer
static const uint8_t TM_BUTTON_HOLD = 0x14;      // emulated push and hold of button
static const uint8_t TM_BUTTON_RELEASE = 0x18;   // emulated release of button
static const uint8_t TM_BUTTON_LOWBAT = 0x80;    // low battery

/////////////////////////////////////// hex codes for different errors ///////////////////////////////////////

static const uint8_t SUCCESS = 0x00;
static const uint8_t ERR_CANCELED = 0x01;
static const uint8_t ERR_OUT_OF_QUEUE = 0x02;
static const uint8_t ERR_INVALID_REQUEST = 0x03;
static const uint8_t ERR_SIZE_MISMATCH = 0x04;
static const uint8_t ERR_INVALID_PARAMETER = 0x05;
static const uint8_t ERR_INCOMPLETE_FW = 0x06;
static const uint8_t ERR_RF_TIMEOUT = 0x07;
static const uint8_t ERR_INVALID_SERIAL = 0x08;
static const uint8_t ERR_SUPERSEDED = 0x09;
static const uint8_t ERR_INCOMPAT_FW = 0x0A;
static const uint8_t ERR_SERIAL_FILTER = 0x0B;
static const uint8_t ERR_FILTER_OUT_OF_MEM = 0x0C;
static const uint8_t ERR_INVALID_SEC_REPLY = 0x0D;
static const uint8_t ERR_TOO_LATE = 0x0E;
static const uint8_t ERR_FAILSTATE = 0xFF;

///////////////////////////////////////// User accessible functions /////////////////////////////////////////

// EwGetFdSerialRequest sends an EW_GET_FD_SERIAL IRP retrieving a serial number from the device
uint8_t EwGetFdSerialRequest(uint16_t Index, uint8_t Serial[16]);

// EwRcvButtonRequest sends an EW_RCV_BUTTON IRP waiting for an easywave telegram from a button event
uint8_t EwRcvButtonRequest(uint8_t *InfoType, uint8_t Transmitter[16], uint8_t InfoData[8]);

// EwSendCmdRequest sends an EW_SEND_CMD IRP sending out a pressed button telegram
uint8_t EwSendCmdRequest(uint8_t Gateway[16], uint8_t Button);

// StartEwSendCmdLoopRequest initiates a handler running EW_SEND_CMDs in a loop, use StopEwSendCmdLoopRequest to stop the loop
void StartEwSendCmdLoopRequest(uint8_t Gateway[16], uint8_t Button);

// StopEwSendCmdLoopRequests stops the loop running Ew_SEND_CMDs
void StopEwSendCmdLoopRequest();

// EwRcvExRequest sends an EW_RCV_EX IRP waiting for an easywave telegram
uint8_t EwRcvExRequest(uint8_t *InfoType, uint8_t Transmitter[16], uint8_t InfoData[8]);

// EwbGetFdSerialRequest sends an EWB_GET_FD_SERIAL IRP to retrieve an EWB Serial Number from Index
uint8_t EwbGetFdSerialRequest(uint16_t Index, uint8_t Serial[16]);

// EwbAddNFilterRequest sends an EWB_ADD_NFILTER IRP to register a EWB_SERIAL_NUMBER
uint8_t EwbAddNFilterRequest(uint8_t Gateway[16]);

// EwbClearNFilterRequest sends an EWB_CLEAR_NFILTER IRP to clear the filter of EWB_SERIAL_NUMBERs
uint8_t EwbClearNFilterRequest();

// EwbJoinDevice sends an EWB_JOIN_DEVICE IRP performing a join procedure
uint8_t EwbJoinDeviceRequest(uint8_t Gateway[16], uint8_t *DeviceType, uint8_t Receiver[16]);

// EwbRemoveDeviceRequest sends an EWB_REMOVE_DEVICE IRP reverting the joining of an Easywave Bidi receiver
uint8_t EwbRemoveDeviceRequest(uint8_t Gateway[16], uint8_t Receiver[16]);

// EwbRcvRequest initiates an EWB_RCV IRP to retrieve incomming EW & EWB messages
uint8_t EwbRcvRequest(uint8_t *InfoType, uint8_t ReceiverTransmitter[16], uint8_t InfoData[8]);

// EwbChangeStateRequest controls an Easywave Bidi receiver with an EWB_CHANGE_STATE IRP
uint8_t EwbChangeStateRequest(uint8_t Gateway[16], uint8_t Receiver[16], uint8_t DesiredMode, uint8_t DesiredState[4], uint8_t *RecentMode, uint8_t RecentState[4]);

// EwbQueryStateRequest retrieves the state of an Easywave Bidi receiver with an EWB_QUERY_STATE IRP
uint8_t EwbQueryStateRequest(uint8_t Gateway[16], uint8_t Receiver[16], uint8_t DesiredMode, uint8_t *RecentMode, uint8_t State[4]);

// EwbTrLrnControlRequest sends an EWB_TRLRN_CONTROL IRP in order to initiate learning or removal of an Easywave transmitter
uint8_t EwbTrLrnControlRequest(uint8_t Gateway[16], uint8_t Receiver[16], uint8_t Ctrl, uint8_t DesiredMode, uint8_t DesiredState[4], uint8_t *RecentMode, uint8_t RecentState[4]);

// SecSendCmdTelRequest transmits a Secwave telegram with a SEC_SEND_CMD_TEL IRP 
uint8_t SecSendCmdTelRequest(uint16_t ButtonNumber, bool bWantReply, bool bIgnoreCmd, uint16_t Cmd, uint8_t Flags, uint16_t *PrimaryState, uint16_t *SecondaryState);

// SecSendLrnTelRequest sends a SEC_SEND_CMD_LRN_TEL IRP transmitting a Secwave learn telegram
uint8_t SecSendLrnTelRequest(uint16_t ButtonNumber, bool bWantReply, bool bIgnoreCmd, uint16_t Cmd, uint8_t Flags, uint16_t *PrimaryState, uint16_t *SecondaryState);

// SecReplyQueryRequest uses a SEC_REPLY_QUERY IRP sending a reply after receiving a telegram
uint8_t SecReplyQueryRequest(uint16_t PrimaryState, uint16_t SecondaryState);

// SecLearnRequest sends a SEC_LEARN IRP to store a Secwave transmitter in a persistent list
uint8_t SecLearnRequest(uint32_t UserData, uint16_t *StorIndex, bool *bWantReply, bool *bIgnoreCmd, uint16_t *Cmd, uint32_t *OUserData, uint8_t *Flags, uint8_t *Learn);

// SecRcvRequest sends a SEC_RCV IRP receiving messages of learned Secwave transmitters
uint8_t SecRcvRequest(uint16_t *StorIndex, bool *bWantReply, bool *bIgnoreCmd, uint16_t *Cmd, uint32_t *UserData, uint8_t *Flags, uint8_t *Learn);

// SecDeleteRequest sends a SEC_DELETE_REQUEST IRP to remove learned Secwave transmitters
uint8_t SecDeleteRequest(uint16_t StorIndex, bool *bIsUsed, uint32_t *UserData);

// SecStatRequest reads out the user data of a stored Secwave transmitter using the SEC_STAT IRP
uint8_t SecStatRequest(uint16_t StorIndex, bool *bIsUsed, uint32_t *UserData);

// SecWrUserDataRequest modifies the user data of a stored Secwave transmitter using the SEC_WR_USERDATA IRP
uint8_t SecWrUserDataRequest(uint16_t StorIndex, uint32_t UserData);

// SecDelteAllRequest sends a SEC_DELETE_ALL IRP clearing all stored Secwave transmitters
uint8_t SecDeleteAllRequest();

// MaQueryHwVerRequest queries the hardware version string with a MA_QUERY_HW_VER IRP
uint8_t MaQueryHwVerRequest(uint8_t HwStr[16]);

// MaQueryFwVerRequest queries the firmware version with a MA_QUERY_FW_VER IRP
uint8_t MaQueryFwVerRequest(uint8_t *MajorVer, uint8_t *MinorVer, bool *bIncompleteFw);

// MaUpdateFwRequest performs the actual firmware update by a sequence of MA_UPDATE_FW IRPs
uint8_t MaUpdateFwRequest(uint32_t FileOffset, uint8_t FileData[16], bool *bRestart);

// CancelAllIoRequest sends a Cancel_ALL_IO IRP request to stop all pending requests
void CancelAllIoRequest();

// Connect is used to start a handler on a serial connection, it should be performed on startup
int Connect(char *portName, bool DebugInfo);

// Dispose is used to clear the serial connection and close active handlers
void Dispose();

#endif // RxModule_