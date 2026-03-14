-- Run only if email/experience_years columns are missing in artist_table.
ALTER TABLE artist_table
    ADD COLUMN email VARCHAR(100) NOT NULL UNIQUE;

ALTER TABLE artist_table
    ADD COLUMN experience_years INT NOT NULL;
