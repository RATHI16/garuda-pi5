import math
import pytest
from bench_turn import BenchTurn, grounded


def test_duration_latches_even_when_heading_does_not_respond():
    b=BenchTurn()
    assert b.step(10,0)
    assert b.step(12.9,0)
    assert not b.step(13,0)
    assert not b.step(20,0)  # repeated Follow cannot reset it
    b.reset()
    assert b.step(21,0)


def test_total_turn_not_net_turn_and_heading_wrap():
    b=BenchTurn()
    assert b.step(0,math.radians(179))
    assert b.step(.1,math.radians(-171))
    assert b.step(.2,math.radians(179))
    assert not b.step(.3,math.radians(-169))
    assert b.state(.3)['turned_deg']==pytest.approx(32)


@pytest.mark.parametrize('args',[(False,False,True,'GROUND'),(True,True,True,'GROUND'),(True,False,False,'GROUND'),(True,False,True,'FLYING')])
def test_mode_change_or_reset_requires_grounded_fresh_disarmed(args):
    with pytest.raises(ValueError):grounded(*args)


def test_grounded_allowed():
    grounded(True,False,True,'GROUND')


def test_nonfinite_and_clock_regression_latch():
    b=BenchTurn();assert not b.step(float('nan'),0)
    b.reset();assert b.step(10,0);assert not b.step(9,0)
