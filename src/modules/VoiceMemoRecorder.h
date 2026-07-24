#pragma once

#include <cstdint>

// Hardware-independent state machine driving the Voice Memo page lifecycle.
//
// This deliberately has NO dependency on the screen, the input broker, Arduino,
// or any board define so it can be exercised directly by the native unit-test
// suite (which builds without HAS_SCREEN). The VoiceMemoModule owns an instance
// of this class and forwards semantic UI events into it; all of the module's
// enter/exit/cancel decisions live here where they are testable.
//
// This first slice only needs IDLE (page not shown) and READY (page shown,
// waiting for a record gesture). Later slices add RECORDING and PLAYING states
// plus the events that reach them.
namespace voicememo
{

enum class State : uint8_t {
    Idle,  // The Voice Memo page is not part of the UI frame carousel.
    Ready, // The Voice Memo page is shown, waiting for a record gesture.
};

// Semantic events the UI layer forwards into the state machine.
enum class Event : uint8_t {
    Open,   // The user asked to open the Voice Memo page.
    Cancel, // The user pressed cancel/back to leave the page.
};

class Recorder
{
  public:
    // The current state.
    State state() const { return state_; }

    // True when the Voice Memo page should be part of the UI frame carousel
    // (i.e. anything other than IDLE).
    bool isPageActive() const { return state_ != State::Idle; }

    // Feed a semantic event into the machine. Returns true when the state
    // actually changed (so the caller can trigger a UI frameset regeneration),
    // false when the event was a no-op in the current state.
    bool handle(Event event)
    {
        // Not yet implemented: the machine ignores every event.
        (void)event;
        return false;
    }

  private:
    State state_ = State::Idle;
};

} // namespace voicememo
