#include "modules/VoiceMemoRecorder.h"
#include <cstdlib> // exit()
#include <unity.h>

using voicememo::Event;
using voicememo::Recorder;
using voicememo::State;

void setUp(void) {}
void tearDown(void) {}

// A freshly constructed recorder is idle and its page is not in the carousel.
void test_initial_state_is_idle()
{
    Recorder r;
    TEST_ASSERT_EQUAL(static_cast<int>(State::Idle), static_cast<int>(r.state()));
    TEST_ASSERT_FALSE(r.isPageActive());
}

// Opening the page from idle enters the READY state, makes the page active, and
// reports that the state changed.
void test_open_from_idle_enters_ready()
{
    Recorder r;
    bool changed = r.handle(Event::Open);
    TEST_ASSERT_TRUE(changed);
    TEST_ASSERT_EQUAL(static_cast<int>(State::Ready), static_cast<int>(r.state()));
    TEST_ASSERT_TRUE(r.isPageActive());
}

// Opening again while already READY is idempotent: state stays READY and no
// change is reported (so the UI is not needlessly regenerated).
void test_open_while_ready_is_noop()
{
    Recorder r;
    r.handle(Event::Open);
    bool changed = r.handle(Event::Open);
    TEST_ASSERT_FALSE(changed);
    TEST_ASSERT_EQUAL(static_cast<int>(State::Ready), static_cast<int>(r.state()));
    TEST_ASSERT_TRUE(r.isPageActive());
}

// Cancelling from READY exits back to IDLE, deactivates the page, and reports
// the change.
void test_cancel_from_ready_returns_to_idle()
{
    Recorder r;
    r.handle(Event::Open);
    bool changed = r.handle(Event::Cancel);
    TEST_ASSERT_TRUE(changed);
    TEST_ASSERT_EQUAL(static_cast<int>(State::Idle), static_cast<int>(r.state()));
    TEST_ASSERT_FALSE(r.isPageActive());
}

// Cancelling while already IDLE is a no-op and reports no change.
void test_cancel_from_idle_is_noop()
{
    Recorder r;
    bool changed = r.handle(Event::Cancel);
    TEST_ASSERT_FALSE(changed);
    TEST_ASSERT_EQUAL(static_cast<int>(State::Idle), static_cast<int>(r.state()));
    TEST_ASSERT_FALSE(r.isPageActive());
}

// A full open -> cancel -> open cycle returns to READY: the page can be
// re-entered after being dismissed.
void test_open_cancel_open_cycle()
{
    Recorder r;
    TEST_ASSERT_TRUE(r.handle(Event::Open));
    TEST_ASSERT_TRUE(r.handle(Event::Cancel));
    TEST_ASSERT_TRUE(r.handle(Event::Open));
    TEST_ASSERT_EQUAL(static_cast<int>(State::Ready), static_cast<int>(r.state()));
    TEST_ASSERT_TRUE(r.isPageActive());
}

void setup()
{
    UNITY_BEGIN();
    RUN_TEST(test_initial_state_is_idle);
    RUN_TEST(test_open_from_idle_enters_ready);
    RUN_TEST(test_open_while_ready_is_noop);
    RUN_TEST(test_cancel_from_ready_returns_to_idle);
    RUN_TEST(test_cancel_from_idle_is_noop);
    RUN_TEST(test_open_cancel_open_cycle);
    exit(UNITY_END());
}

void loop() {}
