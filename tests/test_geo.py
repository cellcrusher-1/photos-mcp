from photos_mcp import geo

EX = [  # three HTC 2026 exchanges
    {"name": "Sandy HS", "lat": 45.405721, "lon": -122.276866},
    {"name": "Proctor Rd", "lat": 45.453705, "lon": -122.290537},
    {"name": "Dee St", "lat": 45.43217, "lon": -122.375395},
]

def test_miles_known():
    # Timberline Lodge to Seaside, about 112 mi as the crow flies
    d = geo.miles(45.3311, -121.7113, 45.9932, -123.9298)
    assert 105 < d < 120

def test_segment_vs_point():
    mid = (45.4297, -122.2837)  # between Sandy and Proctor
    i, d = geo.nearest(*mid, EX, "segments")
    assert i == 0 and d < 0.3
    j, _ = geo.nearest(45.452, -122.29, EX, "points")
    assert j == 1

def test_clean_points_rejects():
    import pytest
    with pytest.raises(ValueError):
        geo.clean_points([{"lat": 99, "lon": 0}])
    with pytest.raises(ValueError):
        geo.clean_points([])
    assert geo.clean_points([{"lat": "45", "lon": "-122"}])[0]["name"] == "#0"
