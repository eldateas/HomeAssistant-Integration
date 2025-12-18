/*
 *  Library for interacting with RxModule - © ELDAT EaS GmbH 2024, L.Koepping
 *
 *  Filename:       RxModule.c
 *  Author:         L.Koepping
 *  Revised:        23.05.2024
 *  Revision:       v1.1.0
 *
 *  Copyright © 2024 by © ELDAT EaS GmbH, All Rights Reserved.
 *  Permission to use, reproduce, copy, prepare derivative works,
 *  modify, distribute, perform, display or sell this software and/or
 *  its documentation for any purpose is prohibited without the express
 *  written consent of ELDAT EaS GmbH.
 */

#include <stdlib.h>
#include <string.h>
#include "RxModule.h" // header file with imports and definitions

/////////////////////////////////////// SOP, EOP marker, byte stuffing ///////////////////////////////////////

static const uint8_t PREFIX = 0x80; // prefix on uint8_t stuffing
static const uint8_t SOP = 0x81;	// start-of-packet
static const uint8_t EOP = 0x82;	// end-of-packet

static const uint8_t STUFFING_MIN = 0x80;			   // start of range which is stuffed
static const uint8_t STUFFING_MAX = 0x82;			   // end of range which is stuffed
static const uint8_t STUFFING_ADDEND = 0xFF & (-0x80); // addend in order to get the replacement

/////////////////////////////////////// hex codes for different functions ///////////////////////////////////////

#define EW_RCV_BUTTON 0x01
#define EW_SEND_CMD 0x02
#define EW_RCV_EX 0x03
#define EWB_JOIN_DEVICE 0x04
#define EWB_REMOVE_DEVICE 0x05
#define EWB_CLEAR_NFILTER 0x06
#define EWB_ADD_NFILTER 0x07
#define EWB_RCV 0x08
#define EWB_CHANGE_STATE 0x09
#define EWB_QUERY_STATE 0x0A
#define EWB_TRLRN_CONTROL 0x0B
#define EW_GET_FD_SERIAL 0x20
#define EWB_GET_FD_SERIAL 0x21
#define TR_ADD_NFILTER 0x30
#define TR_LEARN_U 0x31
#define TR_LEARN 0x32
#define TR_CHANGE_STATE_U 0x33
#define TR_CHANGE_STATE 0x34
#define TR_QUERY_STATE 0x35
#define TR_RCV 0x36
#define SEC_RCV 0xA1
#define SEC_LEARN 0xA2
#define SEC_REPLY_QUERY 0xA3
#define SEC_DELETE 0xA4
#define SEC_STAT 0xA5
#define SEC_WR_USERDATA 0xA6
#define SEC_SEND_CMD_TEL 0xA7
#define SEC_SEND_LRN_TEL 0xA8
#define SEC_DELETE_ALL 0xA9
#define MA_QUERY_HW_VER 0xC0
#define MA_QUERY_FW_VER 0xC1
#define MA_UPDATE_FW 0xC2
#define CANCEL_IO 0xFE
#define CANCEL_ALL_IO 0xFF
#define PING_RCV 0xF0

/////////////////////////////////////// hex codes for test functions ///////////////////////////////////////

///////////////////////////////////////// structs used for params /////////////////////////////////////////

// for CANCEL_IO
typedef struct
{
	uint16_t Handle;
} TM_REQ_CANCEL_IO;

// for EW_SEND_CMD
typedef struct
{
	uint8_t Gateway[16];
	uint8_t Button;
} TM_REQ_SEND_CMD;

// for EWB_JOIN_DEVICE, EWB_ADD_NFILTER, TR_ADD_NFILTER
typedef struct
{
	uint8_t Gateway[16];
} TM_REQ_JOIN_DEVICE;

// for EWB_REMOVE_DEVICE
typedef struct
{
	uint8_t Gateway[16];
	uint8_t Receiver[16];
} TM_REQ_CLEAR_DEVICE;

// for EWB_CHANGE_STATE
typedef struct
{
	uint8_t Gateway[16];
	uint8_t Receiver[16];
	uint8_t Mode;
	uint8_t State[4];
} TM_REQ_CHANGE_STATE_G;

// for EWB_QUERY_STATE
typedef struct
{
	uint8_t Gateway[16];
	uint8_t Receiver[16];
	uint8_t Mode;
} TM_REQ_QUERY_STATE_G;

// for EWB_TRLRN_CONTROL
typedef struct
{
	uint8_t Gateway[16];
	uint8_t Receiver[16];
	uint8_t CtrlFunction;
	uint8_t Mode;
	uint8_t State[4];
} TM_REQ_TRLRN_CONTROL;

// for TR_LEARN_U, TR_LEARN
typedef struct
{
	uint8_t Transmitter[16];
	uint8_t Button;
	uint8_t DeviceType;
} TM_REQ_LEARN_T;

// for TR_CHANGE_STATE_U, TR_CHANGE_STATE
typedef struct
{
	uint8_t Transmitter[16];
	uint8_t Button;
	uint8_t Mode;
	uint8_t State[4];
} TM_REQ_CHANGE_STATE_T;

// for TR_QUERY_STATE
typedef struct
{
	uint8_t Transmitter[16];
	uint8_t Button;
	uint8_t Mode;
} TM_REQ_QUERY_STATE_T;

typedef struct
{
	uint16_t Index;
} TM_REQ_FD_SERIAL;

// for SEC_LEARN
typedef struct
{
	uint32_t UserData;
} TM_REQ_SEC_LRN;

// for SEC_REPLY_QUERY
typedef struct
{
	uint16_t SysState;
	uint16_t AppState;
} TM_REQ_SEC_REPLY_QUERY;

// for SEC_DELETE, SEC_STAT, SEC_WR_USERDATA
typedef struct
{
	uint16_t StorIndex;
	uint32_t UserData; // (used on SEC_WR_USERDATA only)
} TM_REQ_SEC_STOR;

// for SEC_SEND_CMD_TEL, SEC_SEND_LRN_TEL
typedef struct
{
	uint16_t SecButton;
	uint8_t SecQuery;
	uint16_t SecCmd;
	uint8_t SecFlags;
} TM_REQ_SEC_SEND;

// for MA_UPDATE_FW
typedef struct
{
	uint32_t ByteOffset;
	uint8_t FwData[16];
} TM_REQ_UPDATE_FW;

// the IRP
typedef struct
{
	uint8_t Function; // function identifier
	union
	{
		TM_REQ_CANCEL_IO ReqCancelIo;
		TM_REQ_SEND_CMD ReqSendCmd;
		TM_REQ_JOIN_DEVICE ReqJoinDevice;
		TM_REQ_CLEAR_DEVICE ReqClearDevice;
		TM_REQ_CHANGE_STATE_G ReqChangeStateG;
		TM_REQ_QUERY_STATE_G ReqQueryStateG;
		TM_REQ_TRLRN_CONTROL ReqTrLrnControl;
		TM_REQ_LEARN_T ReqLearnT;
		TM_REQ_CHANGE_STATE_T ReqChangeStateT;
		TM_REQ_QUERY_STATE_T ReqQueryStateT;
		TM_REQ_FD_SERIAL ReqFdSerial;
		TM_REQ_SEC_LRN ReqSecLrn;
		TM_REQ_SEC_REPLY_QUERY ReqSecReplyQuery;
		TM_REQ_SEC_STOR ReqSecStor;
		TM_REQ_SEC_SEND ReqSecSend;
		TM_REQ_UPDATE_FW ReqUpdateFw;
	};
} IRP;

// for EW_RCV_BUTTON, EW_RCV_EX, EWB_RCV, TR_RCV
typedef struct
{
	uint8_t InfoType; // a TM_IT_xxx static constant
	uint8_t ReceiverOrTransmitter[16];
	uint8_t InfoData[8];
} TM_CPL_RCV;

// for EWB_JOIN_DEVICE
typedef struct
{
	uint8_t Receiver[16];
	uint8_t DeviceType; // an EWB_DT_xxx static constant
} TM_CPL_JOIN_DEVICE;

// for EWB_CHANGE_STATE, EWB_QUERY_STATE, EWB_TRLRN_CONTROL
typedef struct
{
	uint8_t Mode;
	uint8_t State[4];
} TM_CPL_STATE_G;

// for EW_GET_FD_SERIAL, EW_GET_FD_SERIAL
typedef struct
{
	uint8_t Gateway[16];
} TM_CPL_FD_SERIAL;

// for SEC_RCV, SEC_LEARN
typedef struct
{
	uint16_t StorIndex;
	uint8_t SecQuery;
	uint16_t SecCmd;
	uint32_t UserData;
	uint8_t SecFlags;
	uint8_t LrnTel;
} TM_CPL_SEC_RCV;

// for SEC_DELETE, SEC_STAT
typedef struct
{
	uint8_t IsUsed;
	uint32_t UserData;
} TM_CPL_SEC_STOR;

// for SEC_SEND_CMD_TEL, SEC_SEND_LRN_TEL
typedef struct
{
	uint16_t SysState;
	uint16_t AppState;
} TM_CPL_SEC_SEND;

// for MA_QUERY_HW_VER
typedef struct
{
	uint8_t HwVersionStr[16];
} TM_CPL_HW_VER;

// for MA_QUERY_FW_VER
typedef struct
{
	uint8_t MajorVersion;
	uint8_t MinorVersion;
	uint8_t bIncompleteFw;
} TM_CPL_FW_VER;

// for MA_UPDATE_FW
typedef struct
{
	uint8_t IsRestarting;
} TM_CPL_UPDATE_FW;

// the IPP and ICP
typedef struct
{
	uint16_t Handle;
	uint8_t Result;
	union
	{
		TM_CPL_RCV CplRcv;
		TM_CPL_JOIN_DEVICE CplJoinDevice;
		TM_CPL_STATE_G CplStateG;
		TM_CPL_FD_SERIAL CplFdSerial;
		TM_CPL_SEC_RCV CplSecRcv;
		TM_CPL_SEC_STOR CplSecStor;
		TM_CPL_SEC_SEND CplSecSend;
		TM_CPL_HW_VER CplHwVer;
		TM_CPL_FW_VER CplFwVer;
		TM_CPL_UPDATE_FW CplUpdateFw;
	};
} ICP;

// Request implementation: a helping class for the asynchronous programming model (APM)
typedef struct
{
	bool m_bCompleted;
	bool m_bQueued;
	bool m_bCancel;
	IRP m_Irp;
	int m_IrpByteCount;
	uint16_t m_Handle;
	ICP m_Icp;
	int m_IcpByteCount;
	char m_ReqStr[32];

	pthread_mutex_t m_SignalMutex;
	pthread_cond_t m_Event;
} REQUEST;

// request queue implementation for pending requests
typedef struct
{
	uint16_t Handle;
	REQUEST *r;
} REQUEST_PENDING;
// boolean flag to enable log output
bool DebugInfo = false;

// Graceful shutdown flag for threads
static volatile bool g_ShutdownRequested = false;

/////////////////////////////////////// interaction with rxmodule ///////////////////////////////////////

// the handle for serial port communication
#if defined(_WIN32) || defined(_WIN64)
HANDLE hComm = INVALID_HANDLE_VALUE;
#else
int hComm = -1; // Initialize to invalid file descriptor
#endif

// thread variables for anctive handlers
pthread_t serialHandlerThread = 0;
pthread_t pingHandlerThread = 0;

// implementation of sending IRPs and receiving IPPs and ICPs
pthread_mutex_t m_ProtocolGuard = PTHREAD_MUTEX_INITIALIZER;

// mutex for guarding the write operation to serial bus
pthread_mutex_t m_FileGuard = PTHREAD_MUTEX_INITIALIZER;

// guarded by m_ProtocolGuard ===>
REQUEST *m_TxReqQueued[MAX_REQUEST_QUEUED];
int m_TxReqQueuedRear;
int m_TxReqQueuedFront;
int m_TxReqQueuedSize;
REQUEST *m_TxReqSent[MAX_REQUEST_COUNT];
int m_TxReqSentRear;
int m_TxReqSentFront;
int m_TxReqSentSize;
REQUEST_PENDING *m_ReqPending[MAX_REQUEST_COUNT];
int m_TxReqPendingSize;
bool m_bStateGood;
bool m_RxSop;
int m_RxRawOffset;
uint8_t m_RxRawBuffer[128];
bool m_RxStuffing;
// <=== guarded by m_ProtocolGuard

// remove canceled requests from QueuedRequests
void unguardedRemoveCanceledQueuedRequests()
{
	// check clearing of canceled requests (from front of queue)
	while (m_TxReqQueuedSize > 0 && m_TxReqQueued[m_TxReqQueuedFront] && m_TxReqQueued[m_TxReqQueuedFront]->m_bCancel)
	{
		m_TxReqQueued[m_TxReqQueuedFront] = NULL;
		// increment number of index --> first element
		m_TxReqQueuedFront = (m_TxReqQueuedFront + 1) % MAX_REQUEST_QUEUED;
		// reduce size of the queue
		m_TxReqQueuedSize--;
	}
}

// creates a new REQUEST struct from an Irp and returns it
REQUEST *createRequest(IRP *Irp, int IrpByteCount, int ExpectedIcpByteCount, char *ReqStr)
{
	// create a pointer -> has to be freed after use
	REQUEST *r = (REQUEST *)malloc(sizeof(REQUEST));

	// copy params to request struct
	r->m_Irp = *Irp;
	r->m_IrpByteCount = IrpByteCount;
	r->m_Handle = 0;
	r->m_IcpByteCount = ExpectedIcpByteCount;
	memset(&(r->m_Icp), 0, sizeof(ICP));
	r->m_bQueued = false;
	memcpy(r->m_ReqStr, ReqStr, 32);
	r->m_bCancel = false;
	r->m_bCompleted = false;

	// initialize a mutex for accessing the condition
	pthread_mutex_init(&r->m_SignalMutex, NULL);
	// initialize a condition to allow signaling completion
	pthread_cond_init(&r->m_Event, NULL);

	// return request pointer
	return r;
}

// unguardedReadFromBuffer converts the Buffer from serialHandler and fills in Icp fields
void unguardedReadFromBuffer(ICP *Icp, uint8_t Buffer[128], uint8_t IrpFunction)
{
	uint8_t Offset = 4;

	// There are some IRPs that don't return parameters in ICP:
	// PING_RCV, EWB_REMOVE_DEVICE, EWB_ADD_NFILTER, EWB_CLEAR_NFILTER,
	// EW_SEND_CMD, TR_ADD_NFILTER, SEC_REPLY_QUERY, SEC_DELETE_ALL, SEC_WR_USERDATA

	// use the IrpFunction to define the structure of the received Buffer
	switch (IrpFunction)
	{
	case EWB_JOIN_DEVICE:
		// copy array of uint8
		for (int i = 0; i < 16; i++)
		{
			Icp->CplJoinDevice.Receiver[i] = Buffer[Offset + i];
		}
		Offset = Offset + 16;
		Icp->CplJoinDevice.DeviceType = Buffer[Offset];
		Offset = Offset + 1;
		break;
	case EWB_RCV:
	case TR_RCV:
	case EW_RCV_BUTTON:
	case EW_RCV_EX:
		Icp->CplRcv.InfoType = Buffer[Offset];
		Offset = Offset + 1;
		// copy array of uint8
		for (int i = 0; i < 16; i++)
		{
			Icp->CplRcv.ReceiverOrTransmitter[i] = Buffer[Offset + i];
		}
		Offset = Offset + 16;

		// Additional information. If the type of the received information is 1, the first uint8_t of the field
		// is the button and the function of the Easywave transmitter.
		if (Icp->CplRcv.InfoType == 1)
		{
			// Bit 1-0 is the button, bit 7-2 is the function
			Icp->CplRcv.InfoData[0] = Buffer[Offset];
			Offset = Offset + 1;
		}
		// else if (Icp->CplRcv.InfoType == 0)
		// {
		// content is undetermined
		// }
		else if (Icp->CplRcv.InfoType == 2)
		{
			// the field is the sensor payload data
			for (int i = 0; i < 8; i++)
			{
				Icp->CplRcv.InfoData[i] = Buffer[Offset + i];
			}
			Offset = Offset + 8;
		}
		else if (Icp->CplRcv.InfoType == 3)
		{
			// the first uint8_t of the field is the mode; the next
			// four bytes of the field are the state of the Easywave Bidi receiver
			Icp->CplRcv.InfoData[0] = Buffer[Offset];
			Offset = Offset + 1;

			// copy four uint8
			for (int i = 0; i < 4; i++)
			{
				Icp->CplRcv.InfoData[(1 + i)] = Buffer[Offset + i];
			}
			Offset = Offset + 4;
		}
		// else if (Icp->CplRcv.InfoType >= 40 && (Icp->CplRcv.InfoType <= 42))
		// {
		// field is zeroed out
		// }
		break;
	case EWB_CHANGE_STATE:
		Icp->CplStateG.Mode = Buffer[Offset];
		Offset = Offset + 1;
		// copy four uint8 from Buffer to State array
		for (int i = 0; i < 4; i++)
		{
			Icp->CplStateG.State[i] = Buffer[Offset + i];
		}
		Offset = Offset + 4;
		break;
	case EWB_QUERY_STATE:
		Icp->CplStateG.Mode = Buffer[Offset];
		Offset = Offset + 1;
		// copy four uint8 from Buffer to State array
		for (int i = 0; i < 4; i++)
		{
			Icp->CplStateG.State[i] = Buffer[Offset + i];
		}
		Offset = Offset + 4;
		break;
	case EWB_TRLRN_CONTROL:
		Icp->CplStateG.Mode = Buffer[Offset];
		Offset = Offset + 1;
		// copy four uint8 from Buffer to State array
		for (int i = 0; i < 4; i++)
		{
			Icp->CplStateG.State[i] = Buffer[Offset + i];
		}
		Offset = Offset + 4;
		break;
	case EWB_GET_FD_SERIAL:
		// copy 16 uint8 from Buffer
		for (int i = 0; i < 16; i++)
		{
			Icp->CplFdSerial.Gateway[i] = Buffer[Offset + i];
		}
		Offset = Offset + 16;
		break;
	case EW_GET_FD_SERIAL:
		// copy 16 uint8 from Buffer
		for (int i = 0; i < 16; i++)
		{
			Icp->CplFdSerial.Gateway[i] = Buffer[Offset + i];
		}
		Offset = Offset + 16;
		break;
	case TR_LEARN_U:
		Icp->CplStateG.Mode = Buffer[Offset];
		Offset = Offset + 1;
		// copy four uint8 from Buffer to State array
		for (int i = 0; i < 4; i++)
		{
			Icp->CplStateG.State[i] = Buffer[Offset + i];
		}
		Offset = Offset + 4;
		break;
	case TR_LEARN:
		Icp->CplStateG.Mode = Buffer[Offset];
		Offset = Offset + 1;
		// copy four uint8 from Buffer to State array
		for (int i = 0; i < 4; i++)
		{
			Icp->CplStateG.State[i] = Buffer[Offset + i];
		}
		Offset = Offset + 4;
		break;
	case TR_CHANGE_STATE_U:
		Icp->CplStateG.Mode = Buffer[Offset];
		Offset = Offset + 1;
		// copy four uint8 from Buffer to State array
		for (int i = 0; i < 4; i++)
		{
			Icp->CplStateG.State[i] = Buffer[Offset + i];
		}
		Offset = Offset + 4;
		break;
	case TR_CHANGE_STATE:
		Icp->CplStateG.Mode = Buffer[Offset];
		Offset = Offset + 1;
		// copy four uint8 from Buffer to State array
		for (int i = 0; i < 4; i++)
		{
			Icp->CplStateG.State[i] = Buffer[Offset + i];
		}
		Offset = Offset + 4;
		break;
	case TR_QUERY_STATE:
		Icp->CplStateG.Mode = Buffer[Offset];
		Offset = Offset + 1;
		// copy four uint8 from Buffer to State array
		for (int i = 0; i < 4; i++)
		{
			Icp->CplStateG.State[i] = Buffer[Offset + i];
		}
		Offset = Offset + 4;
		break;
	case SEC_RCV:
		// combine two uint8 to uint16
		Icp->CplSecRcv.StorIndex = ((uint16_t)Buffer[Offset] << 8) | Buffer[Offset + 1];
		Offset = Offset + 2;
		Icp->CplSecRcv.SecQuery = Buffer[++Offset];
		Offset = Offset + 1;
		// combine two uint8 to uint16
		Icp->CplSecRcv.SecCmd = ((uint16_t)Buffer[Offset] << 8) | Buffer[Offset + 1];
		Offset = Offset + 2;
		// combine four uint8 to uint32
		Icp->CplSecRcv.UserData = ((uint32_t)Buffer[Offset] << 24) | ((uint32_t)Buffer[Offset + 1] << 16) | ((uint32_t)Buffer[Offset + 2] << 8) | (uint32_t)Buffer[Offset + 3];
		Offset = Offset + 4;
		Icp->CplSecRcv.SecFlags = Buffer[Offset];
		Offset = Offset + 1;
		Icp->CplSecRcv.LrnTel = Buffer[Offset];
		Offset = Offset + 1;
		break;
	case SEC_LEARN:
		// combine two uint8 to uint16
		Icp->CplSecRcv.StorIndex = ((uint16_t)Buffer[Offset] << 8) | Buffer[Offset + 1];
		Offset = Offset + 2;
		Icp->CplSecRcv.SecQuery = Buffer[Offset];
		Offset = Offset + 1;
		// combine two uint8 to uint16
		Icp->CplSecRcv.SecCmd = ((uint16_t)Buffer[Offset] << 8) | Buffer[Offset + 1];
		Offset = Offset + 2;
		// combine four uint8 to uint32
		Icp->CplSecRcv.UserData = ((uint32_t)Buffer[Offset] << 24) | ((uint32_t)Buffer[Offset + 1] << 16) | ((uint32_t)Buffer[Offset + 2] << 8) | (uint32_t)Buffer[Offset + 3];
		Offset = Offset + 4;
		Icp->CplSecRcv.SecFlags = Buffer[Offset];
		Offset = Offset + 1;
		Icp->CplSecRcv.LrnTel = Buffer[Offset];
		Offset = Offset + 1;
		break;
	case SEC_DELETE:
		Icp->CplSecStor.IsUsed = Buffer[Offset];
		Offset = Offset + 1;
		// combine four uint8 to uint32
		Icp->CplSecStor.UserData = ((uint32_t)Buffer[Offset] << 24) | ((uint32_t)Buffer[Offset + 1] << 16) | ((uint32_t)Buffer[Offset + 2] << 8) | (uint32_t)Buffer[Offset + 3];
		Offset = Offset + 4;
		break;
	case SEC_STAT:
		Icp->CplSecStor.IsUsed = Buffer[Offset];
		Offset = Offset + 1;
		// combine four uint8 to uint32
		Icp->CplSecStor.UserData = ((uint32_t)Buffer[Offset] << 24) | ((uint32_t)Buffer[Offset + 1] << 16) | ((uint32_t)Buffer[Offset + 2] << 8) | (uint32_t)Buffer[Offset + 3];
		Offset = Offset + 4;
		break;
	case SEC_SEND_CMD_TEL:
		// combine two uint8 to uint16
		Icp->CplSecSend.SysState = ((uint16_t)Buffer[Offset] << 8) | Buffer[Offset + 1];
		Offset = Offset + 2;
		// combine two uint8 to uint16
		Icp->CplSecSend.AppState = ((uint16_t)Buffer[Offset] << 8) | Buffer[Offset + 1];
		Offset = Offset + 2;
		break;
	case SEC_SEND_LRN_TEL:
		// combine two uint8 to uint16
		Icp->CplSecSend.SysState = ((uint16_t)Buffer[Offset] << 8) | Buffer[Offset + 1];
		Offset = Offset + 2;
		// combine two uint8 to uint16
		Icp->CplSecSend.AppState = ((uint16_t)Buffer[Offset] << 8) | Buffer[Offset + 1];
		Offset = Offset + 2;
		break;
	case MA_QUERY_HW_VER:
		for (int i = 0; i < 16; i++)
		{
			Icp->CplHwVer.HwVersionStr[i] = Buffer[Offset + i];
		}
		Offset = Offset + 16;
		break;
	case MA_QUERY_FW_VER:
		Icp->CplFwVer.MajorVersion = Buffer[Offset];
		Offset = Offset + 1;
		Icp->CplFwVer.MinorVersion = Buffer[Offset];
		Offset = Offset + 1;
		Icp->CplFwVer.bIncompleteFw = Buffer[Offset];
		Offset = Offset + 1;
		break;
	case MA_UPDATE_FW:
		Icp->CplUpdateFw.IsRestarting = Buffer[Offset];
		Offset = Offset + 1;
		break;
	default:
		// unknown function identifier
		printf("%s %d\n", "[unguardedReadFromBuffer] - unknown function identifier:", IrpFunction);
		break;
	}
}

// writeToBuffer is executed by the serialHandler and Writes Irp-Information to RxModule
void writeToBuffer(IRP Irp, int IrpByteCount, char *ReqStr)
{
	// variable used to determine the number of bytes written so serial connection
#if defined(_WIN32) || defined(_WIN64)
	DWORD dwBytesWritten = 0;
#else
	int dwBytesWritten = 0;
#endif

	// biggest buffer possible to allow expansion through byte stuffing
	uint8_t Buffer[128];

	// size of buffer is depending on irpByteCount. Add two extra slots for SOP and EOP
	uint8_t *RawBuffer;

	// use offset variable to dynamically increase used index
	long unsigned int Offset = 0;

	// add a SOP (Start Of Package) to buffer
	Buffer[Offset] = SOP;

	// add function identifiers to buffer
	Buffer[++Offset] = Irp.Function;

	// add params for Irp functions, following functions have no params:
	// CANCEL_ALL_IO, EWB_CLEAR_NFILTER, EWB_RCV, TR_RCV, SEC_RCV, EW_RCV_BUTTON,
	// SEC_DELETE_ALL, MA_QUERY_HW_VER, MA_QUERY_FW_VER, EW_RCV_EX

	// skip adding params if the IrpByteCount is 1
	if (IrpByteCount > 1)
	{
		switch (Irp.Function)
		{
		case EW_SEND_CMD:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqSendCmd.Gateway[i];
			}
			Buffer[++Offset] = Irp.ReqSendCmd.Button;
			break;
		case EWB_JOIN_DEVICE:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqJoinDevice.Gateway[i];
			}
			break;
		case EWB_REMOVE_DEVICE:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqClearDevice.Gateway[i];
			}
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqClearDevice.Receiver[i];
			}
			break;
		case EWB_ADD_NFILTER:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqJoinDevice.Gateway[i];
			}
			break;
		case EWB_CHANGE_STATE:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqChangeStateG.Gateway[i];
			}
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqChangeStateG.Receiver[i];
			}
			Buffer[++Offset] = Irp.ReqChangeStateG.Mode;
			// loop through uint8 array
			for (int i = 0; i < 4; i++)
			{
				Buffer[++Offset] = Irp.ReqChangeStateG.State[i];
			}
			break;
		case EWB_QUERY_STATE:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqQueryStateG.Gateway[i];
			}
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqQueryStateG.Receiver[i];
			}
			Buffer[++Offset] = Irp.ReqQueryStateG.Mode;
			break;
		case EWB_TRLRN_CONTROL:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqTrLrnControl.Gateway[i];
			}
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqTrLrnControl.Receiver[i];
			}
			Buffer[++Offset] = Irp.ReqTrLrnControl.CtrlFunction;
			Buffer[++Offset] = Irp.ReqTrLrnControl.Mode;
			// loop through uint8 array
			for (int i = 0; i < 4; i++)
			{
				Buffer[++Offset] = Irp.ReqTrLrnControl.State[i];
			}
			break;
		case EW_GET_FD_SERIAL:
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqFdSerial.Index >> 8;	   // high byte
			Buffer[++Offset] = Irp.ReqFdSerial.Index & 0x00FF; // low byte
			break;
		case EWB_GET_FD_SERIAL:
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqFdSerial.Index >> 8;	   // high byte
			Buffer[++Offset] = Irp.ReqFdSerial.Index & 0x00FF; // low byte
			break;
		case TR_ADD_NFILTER:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqJoinDevice.Gateway[i];
			}
			break;
		case TR_LEARN_U:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqLearnT.Transmitter[i];
			}
			Buffer[++Offset] = Irp.ReqLearnT.Button;
			Buffer[++Offset] = Irp.ReqLearnT.DeviceType;
			break;
		case TR_LEARN:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqLearnT.Transmitter[i];
			}
			Buffer[++Offset] = Irp.ReqLearnT.Button;
			Buffer[++Offset] = Irp.ReqLearnT.DeviceType;
			break;
		case TR_CHANGE_STATE_U:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqChangeStateT.Transmitter[i];
			}
			Buffer[++Offset] = Irp.ReqChangeStateT.Button;
			Buffer[++Offset] = Irp.ReqChangeStateT.Mode;
			// loop through uint8 array
			for (int i = 0; i < 4; i++)
			{
				Buffer[++Offset] = Irp.ReqChangeStateT.State[i];
			}
			break;
		case TR_CHANGE_STATE:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqChangeStateT.Transmitter[i];
			}
			Buffer[++Offset] = Irp.ReqChangeStateT.Button;
			Buffer[++Offset] = Irp.ReqChangeStateT.Mode;
			// loop through uint8 array
			for (int i = 0; i < 4; i++)
			{
				Buffer[++Offset] = Irp.ReqChangeStateT.State[i];
			}
			break;
		case TR_QUERY_STATE:
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqQueryStateT.Transmitter[i];
			}
			Buffer[++Offset] = Irp.ReqQueryStateT.Button;
			Buffer[++Offset] = Irp.ReqQueryStateT.Mode;
			break;
		case SEC_LEARN:
			// split uint32 into four uint8
			Buffer[++Offset] = Irp.ReqSecLrn.UserData >> 24;		// first byte
			Buffer[++Offset] = Irp.ReqSecLrn.UserData >> 16;		// second byte
			Buffer[++Offset] = Irp.ReqSecLrn.UserData >> 8;			// third byte
			Buffer[++Offset] = Irp.ReqSecLrn.UserData & 0x000000FF; // last byte
			break;
		case SEC_REPLY_QUERY:
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqSecReplyQuery.SysState >> 8;	   // high byte
			Buffer[++Offset] = Irp.ReqSecReplyQuery.SysState & 0x00FF; // low byte
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqSecReplyQuery.AppState >> 8;	   // high byte
			Buffer[++Offset] = Irp.ReqSecReplyQuery.AppState & 0x00FF; // low byte
			break;
		case SEC_DELETE:
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqSecStor.StorIndex >> 8;	  // high byte
			Buffer[++Offset] = Irp.ReqSecStor.StorIndex & 0x00FF; // low byte
			break;
		case SEC_STAT:
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqSecStor.StorIndex >> 8;	  // high byte
			Buffer[++Offset] = Irp.ReqSecStor.StorIndex & 0x00FF; // low byte
			break;
		case SEC_WR_USERDATA:
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqSecStor.StorIndex >> 8;	  // high byte
			Buffer[++Offset] = Irp.ReqSecStor.StorIndex & 0x00FF; // low byte
			// split uint32 into four uint8
			Buffer[++Offset] = Irp.ReqSecStor.UserData >> 24;		 // first byte
			Buffer[++Offset] = Irp.ReqSecStor.UserData >> 16;		 // second byte
			Buffer[++Offset] = Irp.ReqSecStor.UserData >> 8;		 // third byte
			Buffer[++Offset] = Irp.ReqSecStor.UserData & 0x000000FF; // last byte
			break;
		case SEC_SEND_CMD_TEL:
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqSecSend.SecButton >> 8;	  // high byte
			Buffer[++Offset] = Irp.ReqSecSend.SecButton & 0x00FF; // low byte
			Buffer[++Offset] = Irp.ReqSecSend.SecQuery;
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqSecSend.SecCmd >> 8;	   // high byte
			Buffer[++Offset] = Irp.ReqSecSend.SecCmd & 0x00FF; // low byte
			Buffer[++Offset] = Irp.ReqSecSend.SecFlags;
			break;
		case SEC_SEND_LRN_TEL:
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqSecSend.SecButton >> 8;	  // high byte
			Buffer[++Offset] = Irp.ReqSecSend.SecButton & 0x00FF; // low byte
			Buffer[++Offset] = Irp.ReqSecSend.SecQuery;
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqSecSend.SecCmd >> 8;	   // high byte
			Buffer[++Offset] = Irp.ReqSecSend.SecCmd & 0x00FF; // low byte
			Buffer[++Offset] = Irp.ReqSecSend.SecFlags;
			break;
		case MA_UPDATE_FW:
			// split uint32 into four uint8
			Buffer[++Offset] = Irp.ReqUpdateFw.ByteOffset >> 24;		// first byte
			Buffer[++Offset] = Irp.ReqUpdateFw.ByteOffset >> 16;		// second byte
			Buffer[++Offset] = Irp.ReqUpdateFw.ByteOffset >> 8;			// third byte
			Buffer[++Offset] = Irp.ReqUpdateFw.ByteOffset & 0x000000FF; // last byte
			// loop through uint8 array
			for (int i = 0; i < 16; i++)
			{
				Buffer[++Offset] = Irp.ReqUpdateFw.FwData[i];
			}
			break;
		case CANCEL_IO:
			// split uint16 into two uint8
			Buffer[++Offset] = Irp.ReqCancelIo.Handle >> 8;		// high byte
			Buffer[++Offset] = Irp.ReqCancelIo.Handle & 0x00FF; // low byte
			break;
		default:
			// unknown function identifier
			printf("%s%d\n", "ERROR [writeToBuffer] - unknown function identifier", Irp.Function);
			break;
		}
	}
	Offset++;

	// additional byte stuffing
	for (int i = 1; i < Offset; i++)
	{
		// check if current element in Buffer needs to be stuffed
		if (Buffer[i] >= STUFFING_MIN && Buffer[i] <= STUFFING_MAX)
		{
			// check if the maximum number of bytes in Buffer was exceeded
			if ((Offset + 1) < 128)
			{
				// shift all elements in Buffer one field to the back to make room for new byte created by byte stuffing
				for (int j = Offset; j > i; j--)
				{
					Buffer[j] = Buffer[(j - 1)];
				}
				Offset++;

				// byte stuffing, start from the back
				Buffer[i + 1] = (uint8_t)(Buffer[i + 1] + STUFFING_ADDEND);
				Buffer[i] = PREFIX;

				// increment additionally by one for also increments) -> stuffing byte doesn't need to be checked
				i++;
			}
		}
	}

	// add an EOP (End Of Package) to buffer
	Buffer[Offset] = EOP;
	Offset++;

	// allocate memory to create dynamic array
	RawBuffer = (uint8_t *)calloc(Offset, sizeof(uint8_t));
	memcpy(RawBuffer, Buffer, Offset);

	// lock mutex for new write operation
	pthread_mutex_lock(&m_FileGuard);

#if defined(_WIN32) || defined(_WIN64)
	// Check if handle is valid before writing
	if (hComm == INVALID_HANDLE_VALUE) {
		// Only log this error if we're not in shutdown mode
		if (!g_ShutdownRequested) {
			printf("ERROR [writeToBuffer] - invalid serial handle\n");
		}
		pthread_mutex_unlock(&m_FileGuard);
		return;
	}
	// write to serial port
	WriteFile(hComm, (char *)RawBuffer, Offset, &dwBytesWritten, NULL);
#else
	// Check if file descriptor is valid before writing
	if (hComm < 0) {
		// Only log this error if we're not in shutdown mode
		if (!g_ShutdownRequested) {
			printf("ERROR [writeToBuffer] - invalid serial file descriptor (%d)\n", hComm);
		}
		pthread_mutex_unlock(&m_FileGuard);
		return;
	}
	// write to serial connection (POSIX write returns ssize_t)
	dwBytesWritten = write(hComm, RawBuffer, Offset);
#endif

	// check if the returned number of bytes written matches the expected number ob bytes
	if (dwBytesWritten != Offset)
	{
		// not all bytes written so serial bus
		printf("ERROR [writeToBuffer] - serial bus, %d of %d written\n", dwBytesWritten, (int)Offset);
	}

	// when debug is enabled print out the bytes sent to serial connection
	if (DebugInfo)
	{
		printf("Tx-Uart: ");
		for (int i = 0; i < Offset; i++)
		{
			printf("%02x", RawBuffer[i]);
		}
		printf(" IRP %s\n", ReqStr);
	}

	// unlock for new write operation
	pthread_mutex_unlock(&m_FileGuard);

	// free memory used for dynamic array
	free(RawBuffer);
}

// placeRequest adds a request to the queuedList, the queuedList is then queried by the serialHandler
void placeRequest(REQUEST *r)
{
	// set a lock for changes in the Queue
	pthread_mutex_lock(&m_ProtocolGuard);

	// local healthState is overwritten by global healthState
	if (m_bStateGood)
	{
		// check if an irp can be directly sent to the rxmodule
		// -> there are less than 8 requests sent / pending an no queued requests
		if ((m_TxReqQueuedSize == 0) && !((m_TxReqSentSize + m_TxReqPendingSize) >= MAX_REQUEST_COUNT))
		{
			// insert request to RequestSentQueue
			if (m_TxReqSentFront == -1)
				m_TxReqSentFront = 0;
			// increment index of last element of the queue
			m_TxReqSentRear = (m_TxReqSentRear + 1) % MAX_REQUEST_COUNT;
			m_TxReqSent[m_TxReqSentRear] = r;
			m_TxReqSentSize++;

			// write Irp to SerialConnection
			writeToBuffer(r->m_Irp, r->m_IrpByteCount, r->m_ReqStr);
		}
		// there are queued requests try to add current request to queue
		else if ((m_TxReqQueuedSize < MAX_REQUEST_QUEUED))
		{
			// remove queued requests that have already been canceled to make room
			unguardedRemoveCanceledQueuedRequests();

			// if queue was empty before set beginning to 0
			if (m_TxReqQueuedFront == -1)
				m_TxReqQueuedFront = 0;

			// move index of last element
			m_TxReqQueuedRear = (m_TxReqQueuedRear + 1) % MAX_REQUEST_QUEUED;
			// enqueue request to queuedRequests list
			m_TxReqQueued[m_TxReqQueuedRear] = r;
			// increment size
			m_TxReqQueuedSize++;

			// modify request as queued
			r->m_bQueued = true;
		}
		else
		{
			// request can't be placed because there are to many queued requests
			ICP Icp;
			Icp.Handle = 0;
			Icp.Result = ERR_FAILSTATE;
			r->m_Icp = Icp;
			r->m_IcpByteCount = 3;
			r->m_bCompleted = true;
			pthread_cond_signal(&r->m_Event);
		}
	}
	else
	{
		// complete request as failed because of health
		ICP Icp;
		Icp.Handle = 0;
		Icp.Result = ERR_FAILSTATE;
		r->m_Icp = Icp;
		r->m_IcpByteCount = 3;
		r->m_bCompleted = true;
		pthread_cond_signal(&r->m_Event);
	}

	// release the lock
	pthread_mutex_unlock(&m_ProtocolGuard);
}

// sends an irp to cancel a specific request
void unguardedCancelRequestImpl(REQUEST *r)
{
	IRP CancelIrp;
	CancelIrp.Function = CANCEL_IO;
	CancelIrp.ReqCancelIo.Handle = r->m_Handle;

	char reqStr[32] = "CANCEL_IO";

	writeToBuffer(CancelIrp, 3, reqStr);
}

// cancelIoRequest is used to cancel a specific IoRequest
void cancelIoRequest(REQUEST *r)
{
	bool bQueued;

	// get handle (0 if no handle)
	uint16_t Handle = r->m_Handle;

	bQueued = r->m_bQueued;

	// use mutex for working on queues
	pthread_mutex_lock(&m_ProtocolGuard);
	if (bQueued)
	{
		// mark the request as canceled
		r->m_bCancel = true;
		// call the general removal of all canceled and queued requests
		unguardedRemoveCanceledQueuedRequests();
	}
	// request was already sent -> handle was received
	else if (Handle != 0)
	{
		// cancel specific request through cancel request
		unguardedCancelRequestImpl(r);
	}
	pthread_mutex_unlock(&m_ProtocolGuard);

	// if queued still true
	if (bQueued)
	{
		// manually mark request as canceled
		ICP Icp;
		Icp.Handle = 0;
		Icp.Result = ERR_CANCELED;

		r->m_Icp = Icp;
		r->m_IcpByteCount = 3;
		r->m_bCompleted = true;
		pthread_cond_signal(&r->m_Event);
	}
}

// serialHandler is a dedicated thread that reads incomming messages from the serial connection
void *serialHandler(void *params)
{
	// define a variable used to determine if a new byte was read
#if defined(_WIN32) || defined(_WIN64)
	DWORD dwRead;
#else
	int dwRead;
#endif

	// define a variable to store the next byte read
	char chRead;

	// hold a request pointer used for the current request
	REQUEST *r;

	// use an ICP struct to build request ICP out of received bytes
	ICP Icp;

	// store a byte counter for ICP struct
	int IcpByteCount;

	// run until shutdown requested
	while (!g_ShutdownRequested)
	{
		// lock access to RequestQueues
		pthread_mutex_lock(&m_ProtocolGuard);

		do
		{
			uint8_t d = -1;

#if defined(_WIN32) || defined(_WIN64)
			// use the synchronous ReadFile implementation to reduce overhead (actions are already protected by mutex)
			ReadFile(hComm, &chRead, 1, &dwRead, NULL);
#else
			dwRead = read(hComm, &chRead, 1);
#endif
			if (dwRead != 0)
			{
				d = (uint8_t)chRead;

				// continue as long as there has no error occurred
				if (m_bStateGood)
				{
					// printf("Reading %02x, Offset %i\n", d, m_RxRawOffset);
					m_RxRawBuffer[m_RxRawOffset] = d;
					m_RxRawOffset++;

					// received a StartOfPackage -> new sequence
					if (d == SOP)
					{
						// received two SOPs, last package had not ended
						if (m_RxSop)
						{
							// error occurred
							m_bStateGood = false;
							printf("%s%s\n", "ERROR [RxHandler] - Unexpected data on SOP: ", m_RxRawBuffer);
						}
						else
						{
							// got new SOP
							m_RxSop = true;
							m_RxStuffing = false;

							// reset request
							r = NULL;

							// reset ICP struct
							memset(&Icp, 0, sizeof(Icp));
							Icp.Handle = 0;
							Icp.Result = ERR_FAILSTATE;
							IcpByteCount = 3;
						}
					}
					// current uint8_t is not SOP and we already received a SOP
					else if (m_RxSop)
					{
						// received EndOfPackage -> last Byte of the sequence
						if (d == EOP)
						{
							if (m_RxStuffing)
							{
								m_bStateGood = false;

								printf("%s%s\n", "ERROR [RxHandler] - Unexpected EOP on Unstuffing: ", m_RxRawBuffer);

								m_RxSop = false;
							}
							else
							// a complete packet
							{
								// reset SOP-flag
								m_RxSop = false;

								// sequence only consists of SOP and EOP
								if (m_RxRawOffset < 2)
								{
									m_bStateGood = false;
									printf("%s%s\n", "ERROR [RxHandler] - Package is too small: ", m_RxRawBuffer);
								}
								else
								{
									// Handle (key) consists of the first two Bytes after SOP
									uint16_t Handle = ((uint16_t)m_RxRawBuffer[1] << 8 | m_RxRawBuffer[2]);

									// IcpByteCount is the RxRawOffset without SOP and EOP bytes
									IcpByteCount = m_RxRawOffset - 2;

									// received a single response handle
									if (IcpByteCount == 2)
									{
										// check if ReqSentQueue is empty
										if (m_TxReqSentSize == 0)
										{
											m_bStateGood = false;
											printf("%s%s\n", "ERROR [RxHandler] - Unexpected packet: ", m_RxRawBuffer);
										}
										else
										// move next request from SentRequestQueue to PendingRequestQueue
										{
											// get request struct from SentRequestQueue
											r = m_TxReqSent[m_TxReqSentFront];
											m_TxReqSent[m_TxReqSentFront] = NULL;
											// increment index of first element of sent queue
											m_TxReqSentFront = (m_TxReqSentFront + 1) % MAX_REQUEST_COUNT;
											// increment size of sent queue
											m_TxReqSentSize--;

											Icp.Handle = Handle;

											// zero handle
											if (Handle == 0)
											{
												m_bStateGood = false;
												printf("%s%s\n", "ERROR [RxHandler] - Unexpected zero handle: ", m_RxRawBuffer);
											}
											else
											{
												// check pending requests with same Handle(key)
												// skip if list is empty

												if (m_TxReqPendingSize > 0)
												{
													for (int i = 0; i < MAX_REQUEST_COUNT; i++)
													{
														// there are two IPPs with same Handle
														if (m_ReqPending[i] != NULL && m_ReqPending[i]->Handle == Handle)
														{
															m_bStateGood = false;
															printf("%s%04x\n", "ERROR [RxHandler] - Duplicated handle: ", m_ReqPending[i]->Handle);

															break;
														}
													}
												}

												// handle belongs to request (last one in m_TxReqSent)
												r->m_Handle = Handle;

												// output information about the received IPP to user
												if (DebugInfo)
												{
													printf("Rx-Uart: ");
													for (int i = 0; i < m_RxRawOffset; i++)
													{
														printf("%02x", m_RxRawBuffer[i]);
													}
													printf(" IPP %s", r->m_ReqStr);
													printf(" Handle %04x\n", Handle);
												}

												// check if queue is full
												if (m_TxReqPendingSize < MAX_REQUEST_COUNT)
												{
													// insert request to RequestPendingQueue
													REQUEST_PENDING *r_pend = (REQUEST_PENDING *)malloc(sizeof(REQUEST_PENDING));
													r_pend->Handle = Handle;
													r_pend->r = r;

													// find the first empty spot and save Pending struct
													for (int i = 0; i < MAX_REQUEST_COUNT; i++)
													{
														if (m_ReqPending[i] == NULL)
														{
															m_ReqPending[i] = r_pend;
															break;
														}
													}

													// increment size of request pending queue
													m_TxReqPendingSize++;

													// if request is canceled
													if (r->m_bCancel)
													{
														// execute cancelation of request
														unguardedCancelRequestImpl(r);
													}

													// ignore request furthermore (just wait for ICP)
													r = NULL;
												}
												else
												{
													m_bStateGood = false;
													printf("%s%s\n", "ERROR [RxHandler] - PendingRequestQueue full: \n", m_RxRawBuffer);
												}
											}
										}
									}
									else
									// IcpByteCount != 2 -> response consists of more than a handle (finished request / ICP)
									{
										// get the result of ICP
										Icp.Result = m_RxRawBuffer[3];

										// check if there is a pending request with same Handle(key)
										bool bContainsKey = false;

										// if ICP belongs to synchronous Request, got no Handle
										if (Handle != 0)
										{
											// skip if list is empty
											if ((m_TxReqPendingSize) > 0)
											{
												// search through pending request queue
												for (int i = 0; i < MAX_REQUEST_COUNT; i++)
												{
													if (m_ReqPending[i] != NULL && m_ReqPending[i]->Handle == Handle)
													{
														bContainsKey = true;
														r = m_ReqPending[i]->r;

														// free struct holding Handle and pointer to request
														free(m_ReqPending[i]);

														// clear the entry
														m_ReqPending[i] = NULL;

														m_TxReqPendingSize--;
														break;
													}
												}
											}
										}
										else
										{
											// get request struct from SentRequestQueue
											r = m_TxReqSent[m_TxReqSentFront];
											m_TxReqSent[m_TxReqSentFront] = NULL;
											// increment index of first element of sent queue
											m_TxReqSentFront = (m_TxReqSentFront + 1) % MAX_REQUEST_COUNT;
											// increment size of sent queue
											m_TxReqSentSize--;
										}

										// handle matched request from pending queue
										if (bContainsKey || (Handle == 0))
										{
											// if there is more content in ICP
											if (m_RxRawOffset > 5)
											{
												// fill in ICP struct based on request type
												unguardedReadFromBuffer(&Icp, m_RxRawBuffer, r->m_Irp.Function);
											}

											// the request is completed, check if IcpByteCount doesn't match expected length
											if ((Icp.Result == SUCCESS && (IcpByteCount != r->m_IcpByteCount)))
											{
												m_bStateGood = false;
												printf("%s %02x / %02x\n", "ERROR [RxHandler] - Unexpected ICP length 1: ", IcpByteCount, r->m_IcpByteCount);
												for (int i = 0; i < (IcpByteCount + 2); i++)
												{
													printf("%02x", m_RxRawBuffer[i]);
												}
											}
											// the request is completed, check if Icp contains content
											else if ((Icp.Result != SUCCESS && IcpByteCount != 3))
											{
												m_bStateGood = false;
												printf("%s %02x\n", "ERROR [RxHandler] - Unexpected ICP length 2: ", IcpByteCount);
											}
											else
											{
												if (DebugInfo)
												{
													printf("Rx-Uart: ");
													for (int i = 0; i < m_RxRawOffset; i++)
													{
														printf("%02x", m_RxRawBuffer[i]);
													}
													printf(" ICP %s\n", r->m_ReqStr);
												}

												if (Icp.Result == ERR_OUT_OF_QUEUE)
												{
													// check if queue is smaller thant maximum elements queueable
													if (m_TxReqQueuedSize < MAX_REQUEST_QUEUED)
													{
														// put element back in queue
														if (m_TxReqQueuedFront == -1)
															m_TxReqQueuedFront = 0;
														m_TxReqQueuedRear = (m_TxReqQueuedRear + 1) % MAX_REQUEST_QUEUED;
														m_TxReqQueued[m_TxReqQueuedRear] = r;
														m_TxReqQueuedSize++;

														r = NULL;
													}
													else
													{
														m_bStateGood = false;
														printf("%s%s\n", "ERROR [RxHandler] - QueuedRequest full: ", m_RxRawBuffer);
														return NULL;
													}
												}
												else
												{
													// finish request and signal
													r->m_Icp = Icp;
													r->m_IcpByteCount = IcpByteCount;
													r->m_bCompleted = true;
													pthread_cond_signal(&r->m_Event);
												}
											}
										}
									}
								}

								// package processed, clear m_RxRawBuffer
								memset(m_RxRawBuffer, 0, sizeof(m_RxRawBuffer));
								m_RxRawOffset = 0;
							}
						}
						// got byte stuffing bit
						else if (!m_RxStuffing && (d == PREFIX))
						{
							m_RxStuffing = true;
							// reset offset by one to overwrite prefix
							m_RxRawOffset--;
						}
						else
						{
							// if previous bit was byte stuffing prefix
							if (m_RxStuffing)
							{
								// check if byte needed to be stuffed
								if (d >= ((STUFFING_MIN + STUFFING_ADDEND) & 0xFF) && d <= ((STUFFING_MAX + STUFFING_ADDEND) & 0xFF))
								{
									// remove addend from byte
									d = (d + STUFFING_ADDEND);
									// also update value in buffer
									m_RxRawBuffer[(m_RxRawOffset - 1)] = d;
									m_RxStuffing = false;
								}
								else
								// unexpected stuffing byte
								{
									m_bStateGood = false;
									printf("%s%s\n", "ERROR [RxHandler] - Unexpected stuffing byte on unstuffing: ", m_RxRawBuffer);
									return NULL;
								}
							}

							// while current package is read, check if size exceeds ICP
							if (m_RxSop)
							{
								if (m_RxRawOffset >= (sizeof(ICP) + 2))
								{
									// buffer overflow
									m_RxSop = false;

									m_bStateGood = false;
									printf("%s%s\n", "ERROR [RxHandler] - Unexpected large packet: ", m_RxRawBuffer);
									return NULL;
								}
							}
						}
					}
					// new message didn't start with an SOP
					else
					{
						m_bStateGood = false;
						printf("%s%s\n", "ERROR [RxHandler] - Unexpected byte: ", m_RxRawBuffer);
						return NULL;
					}
				}
			}

			// continue with inner loop until all bytes are read
		} while (dwRead == 1);

		// remove queued requests that have already been canceled to make room
		unguardedRemoveCanceledQueuedRequests();

		// check if number of requests in sent queue and pending queue are higher than the maximum requests the module is capable of
		while ((m_TxReqQueuedSize > 0) && (!((m_TxReqSentSize + m_TxReqPendingSize) >= MAX_REQUEST_COUNT)))
		{
			// get request from dequeueing TxReqQueued
			r = m_TxReqQueued[m_TxReqQueuedFront];
			m_TxReqQueued[m_TxReqQueuedFront] = NULL;
			m_TxReqQueuedFront = (m_TxReqQueuedFront + 1) % MAX_REQUEST_QUEUED;
			m_TxReqQueuedSize--;

			// check if the global health is still good
			if (m_bStateGood)
			{
				// update dequeue status in r
				r->m_bQueued = false;

				// insert request to RequestSentQueue
				if (m_TxReqSentFront == -1)
					m_TxReqSentFront = 0;
				// increment index of last element of the queue
				m_TxReqSentRear = (m_TxReqSentRear + 1) % MAX_REQUEST_COUNT;
				m_TxReqSent[m_TxReqSentRear] = r;
				m_TxReqSentSize++;

				// write IRP to Buffer
				writeToBuffer(r->m_Irp, r->m_IrpByteCount, r->m_ReqStr);
			}
			else
			{
				// complete request as failed because of health
				ICP Icp;
				Icp.Handle = 0;
				Icp.Result = ERR_FAILSTATE;
				r->m_Icp = Icp;
				r->m_IcpByteCount = 3;
				r->m_bCompleted = true;
				pthread_cond_signal(&r->m_Event);
			}
		}

		// release the lock for the QueuedRequestQueue
		pthread_mutex_unlock(&m_ProtocolGuard);

		// small timer to allow for the ProtocolGuard to be used in another thread
		usleep(1000);
	}
	return NULL;
}

///////////////////////////////////////// User accessible functions /////////////////////////////////////////

// EwGetFdSerialRequest executes a EW_GET_FD_SERIAL request to read out a serial number from the RxModule
uint8_t EwGetFdSerialRequest(uint16_t Index, uint8_t Serial[16])
{
	IRP Irp;
	Irp.Function = EW_GET_FD_SERIAL;
	Irp.ReqFdSerial.Index = Index;

	char reqStr[32] = "EW_GET_FD_SERIAL";

	REQUEST *r = createRequest(&Irp, 3, 19, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	memcpy(Serial, r->m_Icp.CplFdSerial.Gateway, 16);

	free(r);

	return Result;
}

// EwRcvButtonRequest sends an EW_RCV_BUTTON IRP to receive an Easywave ButtonPress
uint8_t EwRcvButtonRequest(uint8_t *InfoType, uint8_t Transmitter[16], uint8_t InfoData[8])
{
	IRP Irp;
	Irp.Function = EW_RCV_BUTTON;

	char reqStr[32] = "EW_RCV_BUTTON";

	REQUEST *r = createRequest(&Irp, 1, 21, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*InfoType = r->m_Icp.CplRcv.InfoType;
	memcpy(Transmitter, r->m_Icp.CplRcv.ReceiverOrTransmitter, 16);
	memcpy(InfoData, r->m_Icp.CplRcv.InfoData, 8);

	free(r);

	return Result;
}

// The host can control an Easywave receiver with an EW_SEND_CMD IRP
uint8_t EwSendCmdRequest(uint8_t Gateway[16], uint8_t Button)
{
	IRP Irp;
	Irp.Function = EW_SEND_CMD;
	memcpy(Irp.ReqSendCmd.Gateway, Gateway, 16);
	Irp.ReqSendCmd.Button = Button;

	char reqStr[32] = "EW_SEND_CMD";

	REQUEST *r = createRequest(&Irp, 18, 3, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;
	return Result;
}

// thread variable for handling an EW_SEND_CMD dead man implementation
pthread_t EwSendCmdLoop;

// flag variable used to stop the EW_SEND_CMD loop
bool EwSendCmdLoopRunning = false;

// defined struct for storing the information needed for a DeadMan implementation of EW_SEND_CMD
struct EwSendCmdLoopArgs
{
	uint8_t Gateway[16];
	uint8_t Button;
} EwSendCmdLoopArgs;

// This dead man implementation allows a continuous EW_SEND_CMD request with simple ping-pong
void *ewSendCmdLoopRequest(void *args)
{
	REQUEST *r1 = NULL, *r2 = NULL;
	IRP Irp1, Irp2;
	char reqStr[32] = "EW_SEND_CMD";

	// Start first request
	if (EwSendCmdLoopRunning)
	{
		Irp1.Function = EW_SEND_CMD;
		memcpy(Irp1.ReqSendCmd.Gateway, EwSendCmdLoopArgs.Gateway, 16);
		Irp1.ReqSendCmd.Button = EwSendCmdLoopArgs.Button;
		
		r1 = createRequest(&Irp1, 18, 3, reqStr);
		if (r1 != NULL) {
			placeRequest(r1);
		}
	}

	// Simple ping-pong loop
	while (EwSendCmdLoopRunning && r1 != NULL)
	{
		// Wait for r1 to complete
		pthread_mutex_lock(&r1->m_SignalMutex);
		if (pthread_cond_wait(&r1->m_Event, &r1->m_SignalMutex))
		{
			perror("pthread_cond_wait() error on r1");
			pthread_mutex_unlock(&r1->m_SignalMutex);
			break;
		}
		pthread_mutex_unlock(&r1->m_SignalMutex);
		
		// Clean up r1
		free(r1);
		r1 = NULL;
		
		// Check flag before creating r2
		if (!EwSendCmdLoopRunning) {
			break;
		}
		
		// Start r2
		Irp2.Function = EW_SEND_CMD;
		memcpy(Irp2.ReqSendCmd.Gateway, EwSendCmdLoopArgs.Gateway, 16);
		Irp2.ReqSendCmd.Button = EwSendCmdLoopArgs.Button;
		
		r2 = createRequest(&Irp2, 18, 3, reqStr);
		if (r2 == NULL || !EwSendCmdLoopRunning) {
			break;
		}
		placeRequest(r2);
		
		// Wait for r2 to complete
		pthread_mutex_lock(&r2->m_SignalMutex);
		if (pthread_cond_wait(&r2->m_Event, &r2->m_SignalMutex))
		{
			perror("pthread_cond_wait() error on r2");
			pthread_mutex_unlock(&r2->m_SignalMutex);
			break;
		}
		pthread_mutex_unlock(&r2->m_SignalMutex);
		
		// Clean up r2
		free(r2);
		r2 = NULL;
		
		// Check flag before creating new r1
		if (!EwSendCmdLoopRunning) {
			break;
		}
		
		// Start new r1
		Irp1.Function = EW_SEND_CMD;
		memcpy(Irp1.ReqSendCmd.Gateway, EwSendCmdLoopArgs.Gateway, 16);
		Irp1.ReqSendCmd.Button = EwSendCmdLoopArgs.Button;
		
		r1 = createRequest(&Irp1, 18, 3, reqStr);
		if (r1 == NULL || !EwSendCmdLoopRunning) {
			break;
		}
		placeRequest(r1);
	}
	
	// Final cleanup
	if (r1 != NULL) {
		cancelIoRequest(r1);
		pthread_mutex_lock(&r1->m_SignalMutex);
		pthread_cond_wait(&r1->m_Event, &r1->m_SignalMutex);
		pthread_mutex_unlock(&r1->m_SignalMutex);
		free(r1);
	}
	
	if (r2 != NULL) {
		cancelIoRequest(r2);
		pthread_mutex_lock(&r2->m_SignalMutex);
		pthread_cond_wait(&r2->m_Event, &r2->m_SignalMutex);
		pthread_mutex_unlock(&r2->m_SignalMutex);
		free(r2);
	}
	
	return NULL;
}

// user accessible functio to start a dead man EW_SEND_CMD
void StartEwSendCmdLoopRequest(uint8_t Gateway[16], uint8_t Button)
{
	EwSendCmdLoopArgs.Button = Button;
	memcpy(EwSendCmdLoopArgs.Gateway, Gateway, sizeof(uint8_t) * 16);

	EwSendCmdLoopRunning = true;
	pthread_create(&EwSendCmdLoop, NULL, ewSendCmdLoopRequest, (void *)&EwSendCmdLoopArgs);
}

// user accessible function to stop a dead man EW_SEND_CMD
void StopEwSendCmdLoopRequest()
{
	// Set flag to false to signal the thread to stop
	EwSendCmdLoopRunning = false;
	
	// Give the thread more time to process the flag change
	// This prevents race condition where pthread_join is called
	// before the thread has a chance to see the flag change
	usleep(100000); // 100ms delay to ensure flag processing
	
	// Now wait for thread completion with all cleanup
	pthread_join(EwSendCmdLoop, NULL);
}

// EwRcvRequest sends an EW_RCV_EX Irp to receive incomming messages when RxModule is used as Easyvawe Transceiver
uint8_t EwRcvExRequest(uint8_t *InfoType, uint8_t Transmitter[16], uint8_t InfoData[8])
{
	IRP Irp;
	Irp.Function = EW_RCV_EX;

	char reqStr[32] = "EW_RCV_EX";

	REQUEST *r = createRequest(&Irp, 1, 28, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*InfoType = r->m_Icp.CplRcv.InfoType;
	memcpy(Transmitter, r->m_Icp.CplRcv.ReceiverOrTransmitter, 16);
	memcpy(InfoData, r->m_Icp.CplRcv.InfoData, 8);

	free(r);

	return Result;
}

// With an EwbJoinDeviceRequest the host joins the Easywave Bidi receiver by an EWB_JOIN_DEVICE IRP.
uint8_t EwbJoinDeviceRequest(uint8_t Gateway[16], uint8_t *DeviceType, uint8_t Receiver[16])
{
	IRP Irp;
	Irp.Function = EWB_JOIN_DEVICE;
	memcpy(Irp.ReqJoinDevice.Gateway, Gateway, 16);

	char reqStr[32] = "EWB_JOIN_DEVICE";

	REQUEST *r = createRequest(&Irp, 17, 20, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*DeviceType = r->m_Icp.CplJoinDevice.DeviceType;
	memcpy(Receiver, r->m_Icp.CplJoinDevice.Receiver, 16);

	free(r);

	return Result;
}

// The host can revert the joining of an Easywave Bidi receiver with an EWB_REMOVE_DEVICE IRP
uint8_t EwbRemoveDeviceRequest(uint8_t Gateway[16], uint8_t Receiver[16])
{
	IRP Irp;
	Irp.Function = EWB_REMOVE_DEVICE;
	memcpy(Irp.ReqClearDevice.Gateway, Gateway, 16);
	memcpy(Irp.ReqClearDevice.Receiver, Receiver, 16);

	char reqStr[32] = "EWB_REMOVE_DEVICE";

	REQUEST *r = createRequest(&Irp, 33, 3, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	free(r);

	return Result;
}

// EwbAddNFilterRequest sends an EWB_ADD_NFILTER IRP to add a SerialNumber to communicate with
uint8_t EwbAddNFilterRequest(uint8_t Gateway[16])
{
	IRP Irp;
	Irp.Function = EWB_ADD_NFILTER;
	memcpy(Irp.ReqJoinDevice.Gateway, Gateway, 16);

	char reqStr[32] = "EWB_ADD_NFILTER";

	REQUEST *r = createRequest(&Irp, 17, 3, reqStr);
	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	free(r);

	return Result;
}

// The host also can clear the filter list with an EWB_CLEAR_NFILTER IRP
uint8_t EwbClearNFilterRequest()
{
	IRP Irp;
	Irp.Function = EWB_CLEAR_NFILTER;

	char reqStr[32] = "EWB_CLEAR_NFILTER";

	REQUEST *r = createRequest(&Irp, 1, 3, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	free(r);

	return Result;
}

// The host can control an Easywave Bidi receiver with an EWB_CHANGE_STATE IRP
uint8_t EwbChangeStateRequest(uint8_t Gateway[16], uint8_t Receiver[16], uint8_t DesiredMode, uint8_t DesiredState[4], uint8_t *RecentMode, uint8_t RecentState[4])
{
	IRP Irp;
	Irp.Function = EWB_CHANGE_STATE;
	memcpy(Irp.ReqChangeStateG.Gateway, Gateway, 16);
	memcpy(Irp.ReqChangeStateG.Receiver, Receiver, 16);

	Irp.ReqChangeStateG.Mode = DesiredMode;
	memcpy(Irp.ReqChangeStateG.State, DesiredState, 4);

	char reqStr[32] = "EWB_CHANGE_STATE";

	REQUEST *r = createRequest(&Irp, 38, 8, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*RecentMode = r->m_Icp.CplStateG.Mode;
	memcpy(RecentState, r->m_Icp.CplStateG.State, 4);

	free(r);

	return Result;
}

// The host can query the state of an Easywave Bidi receiver with an EWB_QUERY_STATE IRP
uint8_t EwbQueryStateRequest(uint8_t Gateway[16], uint8_t Receiver[16], uint8_t DesiredMode, uint8_t *RecentMode, uint8_t State[4])
{
	IRP Irp;
	Irp.Function = EWB_QUERY_STATE;
	memcpy(Irp.ReqQueryStateG.Gateway, Gateway, 16);
	memcpy(Irp.ReqQueryStateG.Receiver, Receiver, 16);

	Irp.ReqQueryStateG.Mode = DesiredMode;

	char reqStr[32] = "EWB_QUERY_STATE";

	REQUEST *r = createRequest(&Irp, 34, 8, reqStr);
	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*RecentMode = r->m_Icp.CplStateG.Mode;
	memcpy(State, r->m_Icp.CplStateG.State, 4);

	free(r);

	return Result;
}

// The host can send an EWB_TRLRN_CONTROL IRP in order to initiate the removal of an Easywave transmitter, or abort learning or removal
uint8_t EwbTrLrnControlRequest(uint8_t Gateway[16], uint8_t Receiver[16], uint8_t Ctrl, uint8_t DesiredMode, uint8_t DesiredState[4], uint8_t *RecentMode, uint8_t RecentState[4])
{
	IRP Irp;
	Irp.Function = EWB_TRLRN_CONTROL;
	memcpy(Irp.ReqTrLrnControl.Gateway, Gateway, 16);
	memcpy(Irp.ReqTrLrnControl.Receiver, Receiver, 16);

	Irp.ReqTrLrnControl.CtrlFunction = Ctrl;

	Irp.ReqTrLrnControl.Mode = DesiredMode;
	memcpy(Irp.ReqTrLrnControl.State, DesiredState, 4);

	char reqStr[32] = "EWB_TRLRN_CONTROL";

	REQUEST *r = createRequest(&Irp, 39, 8, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*RecentMode = r->m_Icp.CplStateG.Mode;
	memcpy(RecentState, r->m_Icp.CplStateG.State, 4);

	free(r);

	return Result;
}

// EwbRcvRequest sends and EWB_RCV IRP to receive incomming Easywave and Easywave-Bidi messages
uint8_t EwbRcvRequest(uint8_t *InfoType, uint8_t ReceiverTransmitter[16], uint8_t InfoData[8])
{
	IRP Irp;
	Irp.Function = EWB_RCV;

	char reqStr[32] = "EWB_RCV";

	REQUEST *r = createRequest(&Irp, 1, 28, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*InfoType = r->m_Icp.CplRcv.InfoType;
	memcpy(ReceiverTransmitter, r->m_Icp.CplRcv.ReceiverOrTransmitter, 16);
	memcpy(InfoData, r->m_Icp.CplRcv.InfoData, 8);

	free(r);

	return Result;
}

// EwbGetFdSerialRequest sends an EWB_GET_FD_SERIAL Irp to read out a serial numbers in order to transmit any telegram.
uint8_t EwbGetFdSerialRequest(uint16_t Index, uint8_t Serial[16])
{
	IRP Irp;
	Irp.Function = EWB_GET_FD_SERIAL;
	Irp.ReqFdSerial.Index = Index;

	char reqStr[32] = "EWB_GET_FD_SERIAL";

	REQUEST *r = createRequest(&Irp, 3, 19, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	memcpy(Serial, r->m_Icp.CplFdSerial.Gateway, 16);

	free(r);

	return Result;
}

// SecRcvRequest sends a SEC_RCV IRP to receive incomming Secwave messages
uint8_t SecRcvRequest(uint16_t *StorIndex, bool *bWantReply, bool *bIgnoreCmd, uint16_t *Cmd, uint32_t *UserData, uint8_t *Flags, uint8_t *Learn)
{
	IRP Irp;
	Irp.Function = SEC_RCV;

	char reqStr[32] = "SEC_RCV";

	REQUEST *r = createRequest(&Irp, 1, 14, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;
	*StorIndex = r->m_Icp.CplSecRcv.StorIndex;
	*bWantReply = ((r->m_Icp.CplSecRcv.SecQuery & 1) != 0);
	*bIgnoreCmd = ((r->m_Icp.CplSecRcv.SecQuery & 128) != 0);
	*Cmd = r->m_Icp.CplSecRcv.SecCmd;
	*UserData = r->m_Icp.CplSecRcv.UserData;
	*Flags = r->m_Icp.CplSecRcv.SecFlags;
	*Learn = r->m_Icp.CplSecRcv.LrnTel;

	free(r);

	return Result;
}

// SecLearnRequest sends a SEC_LEARN IRP to learn a secwave serial number from a transmitter
uint8_t SecLearnRequest(uint32_t UserData, uint16_t *StorIndex, bool *bWantReply, bool *bIgnoreCmd, uint16_t *Cmd, uint32_t *OUserData, uint8_t *Flags, uint8_t *Learn)
{
	IRP Irp;
	Irp.Function = SEC_LEARN;
	Irp.ReqSecLrn.UserData = UserData;

	char reqStr[32] = "SEC_LEARN";

	REQUEST *r = createRequest(&Irp, 5, 14, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*StorIndex = r->m_Icp.CplSecRcv.StorIndex;
	*bWantReply = ((r->m_Icp.CplSecRcv.SecQuery & 1) != 0);
	*bIgnoreCmd = ((r->m_Icp.CplSecRcv.SecQuery & 128) != 0);
	*Cmd = r->m_Icp.CplSecRcv.SecCmd;
	*OUserData = r->m_Icp.CplSecRcv.UserData;
	*Flags = r->m_Icp.CplSecRcv.SecFlags;
	*Learn = r->m_Icp.CplSecRcv.LrnTel;

	free(r);

	return Result;
}

// SecReplyQueryRequest sends a SEC_REPLY_QUERY IRP to Reply to an Easywave Device after receiving a message
uint8_t SecReplyQueryRequest(uint16_t PrimaryState, uint16_t SecondaryState)
{
	IRP Irp;
	Irp.Function = SEC_REPLY_QUERY;
	Irp.ReqSecReplyQuery.SysState = PrimaryState;
	Irp.ReqSecReplyQuery.AppState = SecondaryState;

	char reqStr[32] = "SEC_REPLY_QUERY";

	REQUEST *r = createRequest(&Irp, 5, 3, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	free(r);

	return Result;
}

// SecDeleteRequest sends a SEC_DELETE IRP to remove a learned secwave serial number
uint8_t SecDeleteRequest(uint16_t StorIndex, bool *bIsUsed, uint32_t *UserData)
{
	IRP Irp;
	Irp.Function = SEC_DELETE;
	Irp.ReqSecStor.StorIndex = StorIndex;

	char reqStr[32] = "SEC_DELETE";

	REQUEST *r = createRequest(&Irp, 3, 8, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*bIsUsed = (r->m_Icp.CplSecStor.IsUsed != 0);
	*UserData = r->m_Icp.CplSecStor.UserData;

	free(r);

	return Result;
}

// SecStatRequest sends a SEC_STAT IRP to read out the UserData of a stored secwave serial number
uint8_t SecStatRequest(uint16_t StorIndex, bool *bIsUsed, uint32_t *UserData)
{
	IRP Irp;
	Irp.Function = SEC_STAT;
	Irp.ReqSecStor.StorIndex = StorIndex;

	char reqStr[32] = "SEC_STAT";

	REQUEST *r = createRequest(&Irp, 3, 8, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*bIsUsed = (bool)(r->m_Icp.CplSecStor.IsUsed != 0);
	*UserData = r->m_Icp.CplSecStor.UserData;

	free(r);

	return Result;
}

// SecWrUserDataRequest sends a SEC_WR_USERDATA to modify the user data
uint8_t SecWrUserDataRequest(uint16_t StorIndex, uint32_t UserData)
{
	IRP Irp;
	Irp.Function = SEC_WR_USERDATA;
	Irp.ReqSecStor.StorIndex = StorIndex;
	Irp.ReqSecStor.UserData = UserData;

	char reqStr[32] = "SEC_WR_USERDATA";

	REQUEST *r = createRequest(&Irp, 7, 3, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	free(r);

	return Result;
}

// SecSendCmdTelRequest sends a SEC_SEND_CMD_TEL IRP to transmit a Secwave telegram
uint8_t SecSendCmdTelRequest(uint16_t ButtonNumber, bool bWantReply, bool bIgnoreCmd, uint16_t Cmd, uint8_t Flags, uint16_t *PrimaryState, uint16_t *SecondaryState)
{
	IRP Irp;
	Irp.Function = SEC_SEND_CMD_TEL;
	Irp.ReqSecSend.SecButton = ButtonNumber;

	uint8_t Query = 0;
	if (bWantReply)
	{
		Query |= 1;
	}
	if (bIgnoreCmd)
	{
		Query |= 128;
	}
	Irp.ReqSecSend.SecQuery = Query;
	Irp.ReqSecSend.SecCmd = Cmd;
	Irp.ReqSecSend.SecFlags = Flags;

	char reqStr[32] = "SEC_SEND_CMD_TEL";

	REQUEST *r = createRequest(&Irp, 7, 7, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*PrimaryState = r->m_Icp.CplSecSend.SysState;
	*SecondaryState = r->m_Icp.CplSecSend.AppState;

	free(r);

	return Result;
}

// SecSendLrnTelRequest sends a SEC_SEND_LRN_TEL IRP to transmit a Secwave telegram while emulating a pressed learn-key
uint8_t SecSendLrnTelRequest(uint16_t ButtonNumber, bool bWantReply, bool bIgnoreCmd, uint16_t Cmd, uint8_t Flags, uint16_t *PrimaryState, uint16_t *SecondaryState)
{
	IRP Irp;
	Irp.Function = SEC_SEND_LRN_TEL;
	Irp.ReqSecSend.SecButton = ButtonNumber;

	uint8_t Query = 0;
	if (bWantReply)
	{
		Query |= 1;
	}
	if (bIgnoreCmd)
	{
		Query |= 128;
	}
	Irp.ReqSecSend.SecQuery = Query;
	Irp.ReqSecSend.SecCmd = Cmd;
	Irp.ReqSecSend.SecFlags = Flags;

	char reqStr[32] = "SEC_SEND_LRN_TEL";

	REQUEST *r = createRequest(&Irp, 7, 7, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*PrimaryState = r->m_Icp.CplSecSend.SysState;
	*SecondaryState = r->m_Icp.CplSecSend.AppState;

	free(r);

	return Result;
}

// SecDeleteAllRequest sends a SEC_DELETE_ALL IRP to remove all stored secwave serial numbers
uint8_t SecDeleteAllRequest()
{
	IRP Irp;
	Irp.Function = SEC_DELETE_ALL;

	char reqStr[32] = "SEC_DELETE_ALL";

	REQUEST *r = createRequest(&Irp, 1, 3, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	free(r);

	return Result;
}

// MaQueryHwVerRequest sends a MA_QUERY_HW_VER to query the hardware version
uint8_t MaQueryHwVerRequest(uint8_t HwStr[16])
{
	IRP Irp;
	Irp.Function = MA_QUERY_HW_VER;

	char reqStr[32] = "MA_QUERY_HW_VER";

	REQUEST *r = createRequest(&Irp, 1, 19, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	memcpy(HwStr, r->m_Icp.CplHwVer.HwVersionStr, 16);

	free(r);

	return Result;
}

// MaQueryFwVerRequest sends a MA_QUERY_FW_VER to query the firmware version
uint8_t MaQueryFwVerRequest(uint8_t *MajorVer, uint8_t *MinorVer, bool *bIncompleteFw)
{
	IRP Irp;
	Irp.Function = MA_QUERY_FW_VER;

	char reqStr[32] = "MA_QUERY_FW_VER";

	REQUEST *r = createRequest(&Irp, 1, 6, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*MajorVer = r->m_Icp.CplFwVer.MajorVersion;
	*MinorVer = r->m_Icp.CplFwVer.MinorVersion;
	*bIncompleteFw = (r->m_Icp.CplFwVer.bIncompleteFw != 0);

	free(r);

	return Result;
}

// The host performs the actual firmware update by a sequence of MA_UPDATE_FW IRPs
uint8_t MaUpdateFwRequest(uint32_t FileOffset, uint8_t FileData[16], bool *bRestart)
{
	IRP Irp;
	Irp.Function = MA_UPDATE_FW;

	Irp.ReqUpdateFw.ByteOffset = FileOffset;
	memcpy(Irp.ReqUpdateFw.FwData, FileData, 16);

	char reqStr[32] = "MA_UPDATE_FW";

	REQUEST *r = createRequest(&Irp, 21, 4, reqStr);

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	if (pthread_cond_wait(&r->m_Event, &r->m_SignalMutex))
	{
		perror("pthread_cond_wait() error");
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	uint8_t Result = r->m_Icp.Result;

	*bRestart = (r->m_Icp.CplUpdateFw.IsRestarting != 0);

	free(r);

	return Result;
}

// PingRequest sends an unknown function to the RxModule to check if it is responding
uint8_t PingRequest(int duration)
{
	IRP Irp;
	Irp.Function = PING_RCV;

	char reqStr[32] = "PING_RCV";

	REQUEST *r = createRequest(&Irp, 1, 1, reqStr);

	struct timespec ts;

	clock_gettime(CLOCK_REALTIME, &ts);
	ts.tv_sec += duration;

	placeRequest(r);

	pthread_mutex_lock(&r->m_SignalMutex);
	// timedwait is used to return an Error if Request takes to long
	if (pthread_cond_timedwait(&r->m_Event, &r->m_SignalMutex, &ts))
	{
		return ERR_RF_TIMEOUT;
	}
	pthread_mutex_unlock(&r->m_SignalMutex);

	free(r);

	return 0;
}

// CancelAllIoRequest sends a CANCEL_ALL_IO IRP and removes all queued, ongoing or pending requests
void CancelAllIoRequest()
{
	REQUEST *r;
	ICP Icp;

	// run the loop to
	do
	{
		r = NULL;

		// set lock for working on Queue-Array
		pthread_mutex_lock(&m_ProtocolGuard);

		// remove queued Request from Queue-Array;
		if (m_TxReqQueuedSize > 0)
		{
			r = m_TxReqQueued[m_TxReqQueuedFront];
			// increment index of first element
			m_TxReqQueuedFront = (m_TxReqQueuedFront + 1) % MAX_REQUEST_QUEUED;
			// reduce size of queue
			m_TxReqQueuedSize--;
		}

		// release lock
		pthread_mutex_unlock(&m_ProtocolGuard);

		// if request was removed from Queue-Array and not already canceled
		if (r != NULL && !r->m_bCancel)
		{
			// save the canceled state to the request
			Icp.Handle = 0;
			Icp.Result = ERR_CANCELED;

			r->m_Icp = Icp;
			r->m_IcpByteCount = 3;
			r->m_bCompleted = true;
			pthread_cond_signal(&r->m_Event);
		}
	} while (r != NULL);

	// set lock for working on Queue-Array
	pthread_mutex_lock(&m_ProtocolGuard);

	// when QueuedRequestArray is emptied -> send
	IRP CancelIrp;
	CancelIrp.Function = CANCEL_ALL_IO;
	char reqStr[32] = "CANCEL_ALL_IO";

	// send "CANCEL_ALL_IO"-IRP to RxModule
	writeToBuffer(CancelIrp, 1, reqStr);

	for (int i = 0; i < MAX_REQUEST_COUNT; i++)
	{
		// remove handle from the pending queue
		if (m_ReqPending[i] != NULL)
		{
			r = m_ReqPending[i]->r;
			// if request exists in Pending-Array and not already canceled
			if (r != NULL && !r->m_bCancel)
			{
				// save the canceled state to the request
				Icp.Handle = 0;
				Icp.Result = ERR_CANCELED;

				r->m_Icp = Icp;
				r->m_IcpByteCount = 3;
				r->m_bCompleted = true;
				pthread_cond_signal(&r->m_Event);
			}

			// free struct holding Handle and pointer to request
			free(m_ReqPending[i]);

			// clear the entry
			m_ReqPending[i] = NULL;
		}
	}

	m_TxReqPendingSize = 0;

	// release lock
	pthread_mutex_unlock(&m_ProtocolGuard);
}

#if defined(_WIN32) || defined(_WIN32)
// user accessible function connecting to the RXModule, and starting the rxHandler
int Connect(char *portName, bool bDebug)
{
	DebugInfo = bDebug;
	
	// Reset shutdown flag for new connection
	g_ShutdownRequested = false;

	// use global hComm to use in RxHandler
	hComm = CreateFile(portName,
					   GENERIC_READ | GENERIC_WRITE, // access (read and write)
					   0,							 // (share) 0: cannot share the COM port
					   NULL,						 // security (None)
					   OPEN_EXISTING,				 // creation: open_existing
					   0,							 // no overlapped operation
					   NULL);						 // no templates file for COM
	if (hComm == INVALID_HANDLE_VALUE)
	{
		printf("[Connect] - Error opening port.\n");
		return 1;
	}

	//////////////////////////////////////////////////////
	DCB dcb;

	if (!GetCommState(hComm, &dcb)) // get current DCB
	{
		printf("[Connect] - Error GetCommState.\n");
		CloseHandle(hComm);
		return 1;
	}

	// Update DCB rate.
	dcb.BaudRate = CBR_115200; // Setting BaudRate = 115200
	dcb.ByteSize = 8;		   // Setting ByteSize = 8
	dcb.StopBits = ONESTOPBIT; // Setting StopBits = 1
	dcb.Parity = NOPARITY;	   // Setting Parity = None
	dcb.fBinary = TRUE;		   // d has to be TRUE in Windows

	// Set new state.
	if (!SetCommState(hComm, &dcb))
	{
		printf("[Connect] - Error SetCommState.\n");
		CloseHandle(hComm);
		return 1;
	}
	// Error in SetCommState. Possibly a problem with the communications
	// port handle or a problem with the DCB structure itself.

	/////////////////////////////////////////////////////////////////////
	COMMTIMEOUTS timeouts;

	timeouts.ReadIntervalTimeout = 1;
	timeouts.ReadTotalTimeoutMultiplier = 1;
	timeouts.ReadTotalTimeoutConstant = 1;
	timeouts.WriteTotalTimeoutMultiplier = 1;
	timeouts.WriteTotalTimeoutConstant = 1;

	// set timeouts
	if (!SetCommTimeouts(hComm, &timeouts))
	{
		printf("[Connect] - Error SetCommTimeouts.\n");
		CloseHandle(hComm);
		return 1;
	}

	// create a CommMask to receive chars (uint8_t)
	if (!SetCommMask(hComm, EV_RXCHAR))
	{
		printf("[Connect] - Error SetCommMask.\n");
		CloseHandle(hComm);
		return 1;
	}

	// clear the serial interface before establishing read and write thread
	if (!PurgeComm(hComm, PURGE_RXABORT | PURGE_RXCLEAR | PURGE_TXABORT | PURGE_TXCLEAR))
	{
		printf("[Connect] - Error Clearing the Serial interface\n");
		CloseHandle(hComm);
		return 1;
	}

	// reset queue params (front and rear)
	m_TxReqQueuedRear = -1;
	m_TxReqQueuedFront = -1;
	m_TxReqQueuedSize = 0;
	m_TxReqSentRear = -1;
	m_TxReqSentFront = -1;
	m_TxReqQueuedSize = 0;
	m_TxReqQueuedSize = 0;

	// reset params for rxHandler
	m_RxRawOffset = 0;
	memset(m_RxRawBuffer, 0, sizeof(m_RxRawBuffer));
	m_bStateGood = true;
	m_RxSop = false;

	// cancel all ongoing and pending requests
	CancelAllIoRequest();

	// create a designated thread for the serialHandler to run independently from user actions
	if (pthread_create(&serialHandlerThread, NULL, serialHandler, NULL) != 0)
	{
		printf("\n  ERROR ! in creating serialHandler thread");
		return 1;
	}

	if (bDebug)
	{
		printf("Successfully connected to RxModule (%s)!\n", portName);
	}

	return 0;
}

#else

int Connect(char *portName, bool bDebug)
{
	DebugInfo = bDebug;
	
	// Reset shutdown flag for new connection
	g_ShutdownRequested = false;

	// open a filestream at specified port
	hComm = open(portName, O_RDWR | O_NOCTTY | O_SYNC);
	if (hComm < 0)
	{
		printf("[Connect] - Error: opening %s \n", portName);
		return 0;
	}

	// use termios to define serial connection
	struct termios tty;
	if (tcgetattr(hComm, &tty) != 0)
	{
		printf("[Connect] - Error from tcgetattr \n");
		return 0;
	}

	cfsetospeed(&tty, B115200);
	cfsetispeed(&tty, B115200);

	tty.c_cflag = (tty.c_cflag & ~CSIZE) | CS8; // 8-bit chars
	// disable IGNBRK for mismatched speed tests; otherwise receive break
	// as \000 chars
	tty.c_iflag &= ~IGNBRK; // disable break processing
	tty.c_lflag = 0;		// no signaling chars, no echo,
							// no canonical processing
	tty.c_oflag = 0;		// no remapping, no delays
	tty.c_cc[VMIN] = 0;		// read doesn't block
	tty.c_cc[VTIME] = 5;	// 0.5 seconds read timeout

	tty.c_iflag &= ~(IXON | IXOFF | IXANY); // shut off xon/xoff ctrl

	tty.c_cflag |= (CLOCAL | CREAD);   // ignore modem controls,
									   // enable reading
	tty.c_cflag &= ~(PARENB | PARODD); // shut off parity
	tty.c_cflag |= 0;
	tty.c_cflag &= ~CSTOPB;

	if (tcsetattr(hComm, TCSANOW, &tty) != 0)
	{
		printf("[Connect] - Error from tcsetattr");
		return 0;
	}

	// reset queue params (front and rear)
	m_TxReqQueuedRear = -1;
	m_TxReqQueuedFront = -1;
	m_TxReqQueuedSize = 0;
	m_TxReqSentRear = -1;
	m_TxReqSentFront = -1;
	m_TxReqQueuedSize = 0;
	m_TxReqQueuedSize = 0;

	// reset params for rxHandler
	m_RxRawOffset = 0;
	memset(m_RxRawBuffer, 0, sizeof(m_RxRawBuffer));
	m_bStateGood = true;
	m_RxSop = false;

	// cancel all ongoing and pending requests
	CancelAllIoRequest();

	// create a designated thread for the serialHandler to run independently from user actions
	if (pthread_create(&serialHandlerThread, NULL, serialHandler, NULL) != 0)
	{
		printf("\n  ERROR ! in creating handler thread");
		return 1;
	}

	if (bDebug)
	{
		printf("Successfully connected to RxModule (%s)!\n", portName);
	}

	return 0;
}
#endif

// Dispose is used to allow for a controlled exit
void Dispose()
{
	// request graceful shutdown
	g_ShutdownRequested = true;

	// allow loop to observe flag
	usleep(20000); // 20ms grace

	// cancel all ongoing and pending requests (flush queues)
	CancelAllIoRequest();

	// join handler threads if they were created
	if (serialHandlerThread) {
		pthread_join(serialHandlerThread, NULL);
		serialHandlerThread = 0; // Reset thread handle
	}
	if (pingHandlerThread) {
		pthread_join(pingHandlerThread, NULL);
		pingHandlerThread = 0; // Reset thread handle
	}

#if defined(_WIN32) || defined(_WIN64)
	// close the serial connection
	if (hComm != INVALID_HANDLE_VALUE) {
		CloseHandle(hComm);
		hComm = INVALID_HANDLE_VALUE;
	}
#else
	if (hComm >= 0) {
		close(hComm);
		hComm = -1; // Reset to invalid file descriptor
	}
#endif
}