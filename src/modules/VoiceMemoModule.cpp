#include "modules/VoiceMemoModule.h"

#ifdef MESHTASTIC_HAS_VOICEMEMO

#include "graphics/Screen.h" // graphics::screen, FONT_*, isOverlayBannerShowing()

VoiceMemoModule *voiceMemoModule;

// Long trackball press: the gesture that opens the Voice Memo page from the
// normal carousel. In the default classic T-Deck build nothing else navigates on
// INPUT_BROKER_SELECT_LONG -- GamesModule (the other consumer) is compiled out
// unless BASEUI_HAS_GAMES=1, and BuzzerFeedbackThread only plays a click and
// passes the event through. Enabling Games on this board reintroduces the
// conflict; VoiceMemoModule.h fails the build in that case (see the #error there).
static constexpr input_broker_event VOICE_MEMO_OPEN_EVENT = INPUT_BROKER_SELECT_LONG;

VoiceMemoModule::VoiceMemoModule() : SinglePortModule("voicememo", meshtastic_PortNum_PRIVATE_APP)
{
    inputObserver.observe(inputBroker);
}

void VoiceMemoModule::regenerateFrameset()
{
    UIFrameEvent e;
    e.action = UIFrameEvent::Action::REGENERATE_FRAMESET;
    notifyObservers(&e);
}

int VoiceMemoModule::handleInputEvent(const InputEvent *event)
{
    // Never steal input while an alert banner owns the screen.
    if (screen && screen->isOverlayBannerShowing()) {
        return 0;
    }

    // Page closed: the only event of interest is the open gesture; everything
    // else flows on to the normal carousel.
    if (!recorder.isPageActive()) {
        if (event->inputEvent == VOICE_MEMO_OPEN_EVENT) {
            if (recorder.handle(voicememo::Event::Open)) {
                requestFocus();
                regenerateFrameset();
                if (screen) {
                    screen->forceDisplay();
                }
            }
            return 1; // consume the open gesture
        }
        return 0;
    }

    // Page open: cancel/back leaves it and restores the normal frame set.
    if (event->inputEvent == INPUT_BROKER_CANCEL || event->inputEvent == INPUT_BROKER_BACK) {
        if (recorder.handle(voicememo::Event::Cancel)) {
            regenerateFrameset();
            if (screen) {
                screen->forceDisplay();
            }
        }
        return 1;
    }

    // While the page is up we intercept keyboard input, so swallow everything
    // else to keep the carousel from moving underneath us. Later slices handle
    // the record/playback gestures here.
    return 1;
}

void VoiceMemoModule::drawFrame(OLEDDisplay *display, OLEDDisplayUiState *state, int16_t x, int16_t y)
{
    // Keep our frame focused while the page is open.
    requestFocus();

    const int16_t centerX = x + (display->getWidth() / 2);
    int16_t cursorY = y + 2;

    display->setColor(WHITE);
    display->setTextAlignment(TEXT_ALIGN_CENTER);

    display->setFont(FONT_MEDIUM);
    display->drawString(centerX, cursorY, "Voice Memo");
    cursorY += FONT_HEIGHT_MEDIUM + 2;

    display->setFont(FONT_SMALL);
    display->drawString(centerX, cursorY, "READY");
    cursorY += FONT_HEIGHT_SMALL + 4;

    // Hint for the record gesture wired up in a later slice.
    display->drawString(centerX, cursorY, "hold trackball or");
    cursorY += FONT_HEIGHT_SMALL;
    display->drawString(centerX, cursorY, "press Space to record");

    // Restore the default alignment for subsequent frames.
    display->setTextAlignment(TEXT_ALIGN_LEFT);
}

#endif // MESHTASTIC_HAS_VOICEMEMO
