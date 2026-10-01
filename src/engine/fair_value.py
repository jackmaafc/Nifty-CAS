def calculate_fair_value_metrics(ce_price, pe_price, spot_price, futures_price, strike, data_quality, threshold_pct=15.0):
    """
    Implements put-call parity using futures price to reverse-engineer implied carry.
    C - P = F - K * (S / F)
    """
    # Implied discount factor e^(-r*T) = S / F
    # Note: if F == 0, we fallback to S == F
    if futures_price == 0:
        discount_factor = 1.0
        futures_price = spot_price
    else:
        discount_factor = spot_price / futures_price

    theoretical_ce_minus_pe = futures_price - strike * discount_factor
    actual_ce_minus_pe = ce_price - pe_price
    
    raw_deviation = actual_ce_minus_pe - theoretical_ce_minus_pe
    
    if theoretical_ce_minus_pe != 0:
        deviation_pct = (raw_deviation / abs(theoretical_ce_minus_pe)) * 100
    else:
        deviation_pct = float('inf') if raw_deviation > 0 else -float('inf') if raw_deviation < 0 else 0.0
        
    expensive = max(ce_price, pe_price)
    cheap = min(ce_price, pe_price)
    ce_pe_ratio = (expensive / cheap) if cheap > 0 else float('inf')
    
    tradeable_flag = abs(deviation_pct) > threshold_pct
    
    return {
        'theoretical_ce_minus_pe': theoretical_ce_minus_pe,
        'actual_ce_minus_pe': actual_ce_minus_pe,
        'raw_deviation': raw_deviation,
        'deviation_pct': deviation_pct,
        'ce_pe_ratio': ce_pe_ratio,
        'tradeable_flag': tradeable_flag,
        'data_quality': data_quality
    }
