# 0019. The weather request names its model: ERA5-Land temperatures, ERA5 for the rest

Status: accepted 2026-09-30 (#123)

## Context

`weather_url` asked Open-Meteo's archive API for six daily variables and named
no model, and every description of the result called it ERA5 on a 0.25-degree
grid. Open-Meteo's documentation says what an unnamed request gets: a "best
match" that "combines IFS HRES, ERA5 and ERA5-Land seamlessly". Measured against
the archive as it was carried:

- **Through 2016-12-31 the answer is the reanalysis**, and equal to what
  `models=era5_seamless` returns on all six variables: ERA5-Land (0.1°) for the
  three temperatures, ERA5 (0.25°) for precipitation, wind and radiation.
  Re-fetching 2016 under that name changed one day in 366, the last.
- **From 2017-01-01 it is the ECMWF IFS forecast model.** Re-fetching 2017 to
  2026 under `era5_seamless` changed the mean temperature on 94% of rows, by up
  to 8.0°C on one day.
- **The coordinates reported are the IFS cell's in every year**, the years the
  reanalysis answered included.

So the series changed model at 2017, inside the span the weather pages compare
across. Over the 369 complete country-years from 2017 to 2025, heating degree
days from the forecast model sit a median 1.9% above the reanalysis's, more
than 5% away in 100 of them and more than 10% in 54, between 18.7% below and
90.2% above. Ten of the 41 capitals average more than 5% apart: Malta 41.7%,
Cyprus 33.4%, Portugal 24.5%, Greece 15.8% above; Montenegro 12.5% and Georgia
10.0% below. Open-Meteo's own advice for a series over decades is to use ERA5 or
ERA5-Land exclusively.

## Decision

- **Every request carries `models=era5_seamless`** (`WEATHER_MODEL`), and a test
  holds the URL to it.
- **Every carried year is re-fetched under it.** The values from 2017 change,
  and so do the coordinates of every row: the cell reported is ERA5-Land's.
  Locally that is `just backfill-weather`. For the published archive,
  `release-data.yml` takes a `weather_years` input that runs the same recipe
  between the restore and the build, because a routine build asks only for the
  last ninety days and would never reach them.
- **The descriptions say reanalysis, ERA5-Land and 0.1 degrees** where they said
  ERA5 and 0.25, the attribution line included.
- **Two guards for a variable that stops arriving**: a data test that every
  complete country-year has precipitation, wind and radiation, and a pytest
  case that holds the recorded payload to every variable and its unit.

## Rejected

- **Leaving the default and saying where the break is.** The pages compare a
  country with itself across years, and a year-over-year change from 2016 to
  2017 would be a change of model as well as of weather. No label repairs that.
- **`models=era5_land`**, which is what the temperatures are. It answers the
  three temperatures and returns null for precipitation, wind and radiation on
  every date, with a 200 and no message. Nothing here would have failed: every
  range test on those columns passes on a null.
- **`models=era5`**, one model on one grid for all six. Its cell is 0.25°,
  coarser for a capital, and it would change the temperatures of every year
  before 2017 as well: heating degree days from ERA5 run a median 4.1% below
  ERA5-Land's for 2018.
- **The forecast model for every year.** It is the finer grid and runs a day
  behind where the reanalysis runs six, but Open-Meteo serves it from 2017 only,
  and the archive starts in 2007.
- **A model column on each row.** One request parameter decides it for the
  whole table, so the column would hold one value. While an archive is part
  re-fetched the cell says which rows are which: more than one
  `(grid_latitude, grid_longitude)` for a country means a year is still on the
  old answer.

## Consequences

The newest day in the archive is about six days old where it was three: the
days the reanalysis has not reached come back with every variable null and are
dropped. `WEATHER_END_LAG_DAYS` stays at three, because it guards the API's own
end date and not the model's.

The figures the weather pages compute moved with the rows: the panel's
year-level correlation between heating demand and CO₂ is 0.69 where it was
0.73, and `hdd_minmax_total` is the larger of the two conventions in 49.7% of
country-years where it was 38.6%. The finding against prices did not move.

A release built before its archive has been re-fetched holds both answers, and
its descriptions are wrong for the years not yet re-fetched. All sixteen carried
years cost about 10,100 units against a daily allowance of 10,000, so the
re-fetch is two runs on two days.
