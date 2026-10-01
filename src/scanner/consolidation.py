def check_consolidation_a(spot_prices, timestamps, current_time, range_points=30, duration_min=15):
    """
    Condition A - Price Range
    Spot has been oscillating within `range_points` of a strike for at least `duration_min`
    immediately before the candidate moment `current_time`.
    `timestamps` should be datetime objects ending at current_time.
    """
    if not spot_prices or not timestamps:
        return False, 0.0, 0.0
    
    # Filter to the lookback window
    # Assuming timestamps are sorted
    import datetime
    
    # Handle int/float timestamps vs datetime objects
    if isinstance(current_time, datetime.datetime):
        start_time = current_time - datetime.timedelta(minutes=duration_min)
    else:
        start_time = current_time - (duration_min * 60)
    
    window_spots = []
    actual_start_time = None
    for ts, spot in zip(timestamps, spot_prices):
        if ts >= start_time and ts <= current_time:
            window_spots.append(spot)
            if actual_start_time is None:
                actual_start_time = ts
                
    if not window_spots:
        return False, 0.0, 0.0
        
    actual_range = max(window_spots) - min(window_spots)
    if isinstance(current_time, datetime.datetime):
        actual_duration = (current_time - actual_start_time).total_seconds() / 60 if actual_start_time else 0.0
    else:
        actual_duration = (current_time - actual_start_time) / 60 if actual_start_time else 0.0
    
    condition_a_met = actual_range <= range_points and actual_duration >= (duration_min * 0.9) # Give a small 10% leeway for discrete intervals
    
    return condition_a_met, actual_range, actual_duration

def check_consolidation_b(oi_strike, oi_adjacent, threshold=2.0):
    """
    Condition B - OI Buildup
    OI at candidate strike is materially higher than OI at adjacent strikes.
    """
    if not oi_adjacent or sum(oi_adjacent) == 0:
        return False, 0.0
        
    avg_adjacent = sum(oi_adjacent) / len(oi_adjacent)
    if avg_adjacent == 0:
        return True, float('inf') if oi_strike > 0 else 0.0
        
    oi_ratio = oi_strike / avg_adjacent
    condition_b_met = oi_ratio >= threshold
    
    return condition_b_met, oi_ratio

def check_consolidation_confluence(spot_prices, timestamps, current_time, 
                                   oi_strike, oi_adjacent,
                                   range_points=30, duration_min=15, oi_threshold=2.0):
    cond_a_met, actual_range, actual_duration = check_consolidation_a(
        spot_prices, timestamps, current_time, range_points, duration_min
    )
    cond_b_met, oi_ratio = check_consolidation_b(
        oi_strike, oi_adjacent, oi_threshold
    )
    
    return {
        'condition_a_met': cond_a_met,
        'actual_range': actual_range,
        'actual_duration': actual_duration,
        'condition_b_met': cond_b_met,
        'oi_ratio': oi_ratio,
        'consolidation_confluence_met': cond_a_met and cond_b_met
    }
