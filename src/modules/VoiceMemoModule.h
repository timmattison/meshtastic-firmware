#pragma once

#include "configuration.h"

// The Voice Memo page only exists on the classic-UI LilyGo T-Deck (and T-Deck
// Plus): a device with the ES7210 microphone, an I2S DAC, and PSRAM. The
// t-deck-tft / MUI build (HAS_TFT) draws its menus from the external
// meshtastic/device-ui project and is excluded here; every other target
// compiles the module out entirely and pays no flash cost.
//
// configuration.h always defines HAS_TFT (0 or 1), so it must be tested by value
// (!HAS_TFT), never with defined(). T_DECK and HAS_I2S are bare defines from the
// board's build flags / variant.h, so they use defined().
#if HAS_SCREEN && !HAS_TFT && defined(T_DECK) && defined(HAS_I2S) && !defined(MESHTASTIC_EXCLUDE_VOICEMEMO)
#define MESHTASTIC_HAS_VOICEMEMO 1
#endif

// INPUT_BROKER_SELECT_LONG opens the Voice Memo page (see VoiceMemoModule.cpp).
// GamesModule binds the same long-press, and the InputBroker observer chain
// short-circuits on the first consumer that returns non-zero (Observer.h), so with
// both modules compiled in one silently shadows the other's SELECT_LONG. Fail the
// build loudly rather than ship that conflict; give one of them a distinct gesture
// before enabling both on a single board. BASEUI_HAS_GAMES is always defined (0/1)
// by configuration.h, so test it by value; MESHTASTIC_HAS_VOICEMEMO is only
// conditionally defined, so test it with defined().
#if defined(MESHTASTIC_HAS_VOICEMEMO) && BASEUI_HAS_GAMES
#error "Voice Memo and Games both bind INPUT_BROKER_SELECT_LONG; resolve the gesture conflict before enabling both."
#endif

#ifdef MESHTASTIC_HAS_VOICEMEMO

#include "SinglePortModule.h"
#include "input/InputBroker.h"
#include "modules/VoiceMemoRecorder.h"

// Full-screen "Voice Memo" page for the classic T-Deck UI.
//
// Follows the CannedMessageModule launched-page pattern: the module observes the
// InputBroker; while idle it watches for the open gesture (a long trackball
// press, INPUT_BROKER_SELECT_LONG, which the classic carousel leaves unused).
// Opening the page requests screen focus and inserts a frame into the carousel;
// cancel/back removes the frame and restores the normal frame set.
//
// This slice is UI plumbing only -- no audio. Every enter/exit/cancel decision
// lives in the hardware-independent voicememo::Recorder (unit-tested natively);
// this class is only the thin T-Deck adapter around it.
class VoiceMemoModule : public SinglePortModule, public Observable<const UIFrameEvent *>
{
  public:
    VoiceMemoModule();

    // This module neither sends nor receives mesh packets.
    virtual bool wantPacket(const meshtastic_MeshPacket *p) override { return false; }

  protected:
    // === UI frame plumbing (see MeshModule / Screen::setFrames) ===
    virtual bool wantUIFrame() override { return recorder.isPageActive(); }
    virtual Observable<const UIFrameEvent *> *getUIFrameObservable() override { return this; }
    virtual bool interceptingKeyboardInput() override { return recorder.isPageActive(); }
    virtual void drawFrame(OLEDDisplay *display, OLEDDisplayUiState *state, int16_t x, int16_t y) override;

  private:
    int handleInputEvent(const InputEvent *event);
    CallbackObserver<VoiceMemoModule, const InputEvent *> inputObserver =
        CallbackObserver<VoiceMemoModule, const InputEvent *>(this, &VoiceMemoModule::handleInputEvent);

    // Ask Screen to rebuild the carousel: adds or removes our frame (per
    // wantUIFrame()) and honors requestFocus().
    void regenerateFrameset();

    voicememo::Recorder recorder;
};

extern VoiceMemoModule *voiceMemoModule;

#endif // MESHTASTIC_HAS_VOICEMEMO
