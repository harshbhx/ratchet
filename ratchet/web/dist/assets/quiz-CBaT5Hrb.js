var e=`{
 "source": "harshbhx/ratchet-text2sql-exams",
 "evidence": [
  "evidence/text2sql-round1",
  "evidence/text2sql-round2",
  "evidence/text2sql-exam2"
 ],
 "scoring": "an answer is right when running it returns the same rows as the reference query on the same database",
 "rows": [
  {
   "exam": "exam1",
   "i": 3,
   "domain": "airline operations",
   "difficulty": "join",
   "question": "Show each flight with its route code, origin city, and destination city.",
   "reference": "SELECT f.flight_number, r.route_code, oh.city AS origin_city, dh.city AS destination_city FROM aero_horizon_flights f JOIN aero_horizon_routes r ON f.route_id = r.route_id JOIN aero_horizon_hubs oh ON r.origin_hub_id = oh.hub_id JOIN aero_horizon_hubs dh ON r.destination_hub_id = dh.hub_id;",
   "nano": {
    "sql": "SELECT f.flight_number, r.route_code, oh_origin.city AS origin_city, oh_dest.city AS destination_city FROM aero_horizon_flights f JOIN aero_horizon_routes r ON f.route_id = r.route_id JOIN aero_horizon_hubs oh_origin ON r.origin_hub_id = oh_origin.hub_id JOIN aero_horizon_hubs oh_dest ON r.destination_hub_id = oh_dest.hub_id ORDER BY f.flight_number;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT f.flight_number, r.route_code, oh.city AS origin_city, dh.city AS destination_city\\nFROM aero_horizon_flights f\\nJOIN aero_horizon_routes r ON f.route_id = r.route_id\\nJOIN aero_horizon_hubs oh ON r.origin_hub_id = oh.hub_id\\nJOIN aero_horizon_hubs dh ON r.destination_hub_id = dh.hub_id;",
    "right": true
   },
   "base": {
    "sql": "SELECT f.flight_number, r.route_code, o.city AS origin_city, d.city AS destination_city\\nFROM aero_horizon_flights f\\nJOIN aero_horizon_routes r ON f.route_id = r.route_id\\nJOIN aero_horizon_hubs o ON r.origin_hub_id = o.hub_id\\nJOIN aero_horizon_hubs d ON r.destination_hub_id = d.hub_id;",
    "right": true
   }
  },
  {
   "exam": "exam1",
   "i": 14,
   "domain": "pharmacy inventory",
   "difficulty": "join",
   "question": "Identify products supplied by inactive suppliers.",
   "reference": "SELECT p.product_name, s.company_name FROM products p JOIN suppliers s ON p.supplier_id = s.supplier_id WHERE s.is_active = 0;",
   "nano": {
    "sql": "SELECT p.product_name, s.company_name AS supplier_name FROM products p JOIN suppliers s ON p.supplier_id = s.supplier_id WHERE s.is_active = 0;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT p.product_id, p.sku, p.product_name, s.supplier_id, s.company_name\\nFROM products p\\nJOIN suppliers s ON p.supplier_id = s.supplier_id\\nWHERE s.is_active = 0;",
    "right": false
   },
   "base": {
    "sql": "SELECT p.product_id, p.product_name, s.company_name, s.phone_number, s.email\\nFROM products p\\nJOIN suppliers s ON p.supplier_id = s.supplier_id\\nWHERE s.is_active = 0;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 28,
   "domain": "clinic appointments",
   "difficulty": "simple",
   "question": "List all active medical facilities with their city and state.",
   "reference": "SELECT facility_name, city, state FROM medical_facilities WHERE is_active = 1 ORDER BY facility_name;",
   "nano": {
    "sql": "SELECT facility_name, city, state FROM medical_facilities WHERE is_active = 1 ORDER BY facility_name;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT facility_name, city, state FROM medical_facilities WHERE is_active = 1;",
    "right": false
   },
   "base": {
    "sql": "SELECT facility_name, city, state \\nFROM medical_facilities \\nWHERE is_active = 1;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 29,
   "domain": "clinic appointments",
   "difficulty": "simple",
   "question": "Show the 5 most recently hired care providers who are currently active.",
   "reference": "SELECT first_name, last_name, medical_specialty, hire_date FROM care_providers WHERE employment_status = 'Active' ORDER BY hire_date DESC LIMIT 5;",
   "nano": {
    "sql": "SELECT first_name, last_name, medical_specialty, hire_date FROM care_providers WHERE employment_status = 'Active' ORDER BY date(REPLACE(hire_date, '/', '-')) DESC LIMIT 5;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT provider_id, first_name, last_name, medical_specialty, hire_date\\nFROM care_providers\\nWHERE employment_status = 'Active'\\nORDER BY date(REPLACE(hire_date, '/', '-')) DESC\\nLIMIT 5;",
    "right": false
   },
   "base": {
    "sql": "SELECT provider_id, first_name, last_name, medical_specialty, hire_date\\nFROM care_providers\\nWHERE employment_status = 'Active'\\nORDER BY hire_date DESC\\nLIMIT 5;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 35,
   "domain": "clinic appointments",
   "difficulty": "aggregate",
   "question": "Calculate the average copay amount by facility for completed visits.",
   "reference": "SELECT mf.facility_name, ROUND(AVG(sv.copay_amount), 2) AS avg_copay FROM scheduled_visits sv JOIN medical_facilities mf ON sv.facility_id = mf.facility_id WHERE sv.visit_status = 'Completed' GROUP BY mf.facility_name ORDER BY avg_copay DESC;",
   "nano": {
    "sql": "SELECT mf.facility_name, ROUND(AVG(sv.copay_amount), 2) AS avg_copay FROM scheduled_visits sv JOIN medical_facilities mf ON sv.facility_id = mf.facility_id WHERE sv.visit_status = 'Completed' GROUP BY mf.facility_id, mf.facility_name ORDER BY avg_copay DESC;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT mf.facility_name, AVG(sv.copay_amount) AS average_copay\\nFROM scheduled_visits sv\\nJOIN medical_facilities mf ON sv.facility_id = mf.facility_id\\nWHERE sv.visit_status = 'Completed'\\nGROUP BY mf.facility_id, mf.facility_name;",
    "right": false
   },
   "base": {
    "sql": "SELECT AVG(copay_amount) AS avg_copay\\nFROM scheduled_visits\\nWHERE visit_status = 'Completed'\\nGROUP BY facility_id;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 49,
   "domain": "university courses",
   "difficulty": "simple",
   "question": "Find the 5 most recently hired facilitators, showing their names and hire dates.",
   "reference": "SELECT full_name, hire_date FROM facilitator ORDER BY hire_date DESC LIMIT 5;",
   "nano": {
    "sql": "SELECT full_name, hire_date FROM facilitator ORDER BY hire_date DESC LIMIT 5;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT full_name, hire_date FROM facilitator ORDER BY date(REPLACE(hire_date, '/', '-')) DESC LIMIT 5;",
    "right": true
   },
   "base": {
    "sql": "SELECT full_name, hire_date FROM facilitator ORDER BY hire_date DESC LIMIT 5;",
    "right": true
   }
  },
  {
   "exam": "exam1",
   "i": 63,
   "domain": "video game studio",
   "difficulty": "aggregate",
   "question": "For each project, count the total number of assets and calculate the average file size in megabytes.",
   "reference": "SELECT gp.title, COUNT(a.asset_id) AS asset_count, ROUND(AVG(a.file_size_mb), 2) AS avg_file_size_mb FROM game_project gp LEFT JOIN asset a ON gp.project_id = a.project_id GROUP BY gp.project_id, gp.title ORDER BY asset_count DESC;",
   "nano": {
    "sql": "SELECT gp.title, COUNT(a.asset_id) AS total_assets, ROUND(AVG(a.file_size_mb), 2) AS avg_file_size_mb FROM game_project gp LEFT JOIN asset a ON gp.project_id = a.project_id GROUP BY gp.project_id, gp.title ORDER BY total_assets DESC;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT project_id, COUNT(*) AS total_assets, AVG(file_size_mb) AS avg_file_size_mb FROM asset GROUP BY project_id;",
    "right": false
   },
   "base": {
    "sql": "SELECT \\n    gp.project_id,\\n    COUNT(a.asset_id) AS total_assets,\\n    AVG(a.file_size_mb) AS avg_file_size_mb\\nFROM \\n    game_project gp\\nJOIN \\n    asset a ON gp.project_id = a.project_id\\nGROUP BY \\n    gp.project_id;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 90,
   "domain": "farm harvests",
   "difficulty": "join",
   "question": "Show the destination market and revenue for every shipment linked to the 'Alpha Pickers' crew.",
   "reference": "SELECT ms.destination_market, ms.revenue_usd\\nFROM market_shipments ms\\nJOIN processing_batches pb ON ms.batch_id = pb.batch_id\\nJOIN harvest_events he ON pb.event_id = he.event_id\\nJOIN harvest_crews hc ON he.crew_id = hc.crew_id\\nWHERE hc.crew_name = 'Alpha Pickers'\\nORDER BY ms.shipment_id;",
   "nano": {
    "sql": "SELECT ms.destination_market, ms.revenue_usd FROM market_shipments ms JOIN processing_batches pb ON ms.batch_id = pb.batch_id JOIN harvest_events he ON pb.event_id = he.event_id JOIN harvest_crews hc ON he.crew_id = hc.crew_id WHERE hc.crew_name = 'Alpha Pickers';",
    "right": true
   },
   "ultra": {
    "sql": "SELECT ms.destination_market, ms.revenue_usd\\nFROM market_shipments ms\\nJOIN processing_batches pb ON ms.batch_id = pb.batch_id\\nJOIN harvest_events he ON pb.event_id = he.event_id\\nJOIN harvest_crews hc ON he.crew_id = hc.crew_id\\nWHERE hc.crew_name = 'Alpha Pickers';",
    "right": true
   },
   "base": {
    "sql": "SELECT m.destination_market, m.revenue_usd\\nFROM market_shipments m\\nJOIN processing_batches p ON m.batch_id = p.batch_id\\nJOIN harvest_events e ON p.event_id = e.event_id\\nJOIN harvest_crews c ON e.crew_id = c.crew_id\\nWHERE c.crew_name = 'Alpha Pickers';",
    "right": true
   }
  },
  {
   "exam": "exam1",
   "i": 98,
   "domain": "event ticketing",
   "difficulty": "join",
   "question": "For every completed event, show the event title, venue name, and city.",
   "reference": "SELECT e.title, v.name, v.city FROM event e JOIN venue v ON e.venue_id = v.venue_id WHERE e.status = 'Completed';",
   "nano": {
    "sql": "SELECT e.title, v.name AS venue_name, v.city FROM event e JOIN venue v ON e.venue_id = v.venue_id WHERE e.status = 'Completed' ORDER BY e.start_datetime;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT e.title, v.name, v.city\\nFROM event e\\nJOIN venue v ON e.venue_id = v.venue_id\\nWHERE e.status = 'Completed';",
    "right": true
   },
   "base": {
    "sql": "SELECT e.title, v.name, v.city\\nFROM event e\\nJOIN venue v ON e.venue_id = v.venue_id\\nWHERE e.status = 'Completed';",
    "right": true
   }
  },
  {
   "exam": "exam1",
   "i": 99,
   "domain": "event ticketing",
   "difficulty": "join",
   "question": "List all purchases for the Apex Leadership Summit 2024 including attendee name, ticket type, quantity, and unit price paid.",
   "reference": "SELECT a.full_name, tt.name, p.quantity, p.unit_price FROM purchase p JOIN attendee a ON p.attendee_id = a.attendee_id JOIN ticket_type tt ON p.ticket_type_id = tt.ticket_type_id JOIN event e ON tt.event_id = e.event_id WHERE e.title = 'Apex Leadership Summit 2024';",
   "nano": {
    "sql": "SELECT a.full_name, tt.name AS ticket_type, p.quantity, p.unit_price FROM purchase p JOIN attendee a ON p.attendee_id = a.attendee_id JOIN ticket_type tt ON p.ticket_type_id = tt.ticket_type_id JOIN event e ON tt.event_id = e.event_id WHERE e.title = 'Apex Leadership Summit 2024' ORDER BY p.purchase_datetime;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT a.full_name AS attendee_name, tt.name AS ticket_type, p.quantity, p.unit_price\\nFROM purchase p\\nJOIN attendee a ON p.attendee_id = a.attendee_id\\nJOIN ticket_type tt ON p.ticket_type_id = tt.ticket_type_id\\nJOIN event e ON tt.event_id = e.event_id\\nWHERE e.title = 'Apex Leadership Summit 2024';",
    "right": true
   },
   "base": {
    "sql": "SELECT a.full_name, tt.name AS ticket_type, p.quantity, p.unit_price\\nFROM purchase p\\nJOIN attendee a ON p.attendee_id = a.attendee_id\\nJOIN ticket_type tt ON p.ticket_type_id = tt.event_id\\nJOIN event e ON tt.event_id = e.event_id\\nWHERE e.title = 'Apex Leadership Summit 2024';",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 106,
   "domain": "airline operations",
   "difficulty": "simple",
   "question": "Show the five longest routes by distance, including origin, destination, and distance in kilometers.",
   "reference": "SELECT origin_code, destination_code, distance_km FROM routes ORDER BY distance_km DESC LIMIT 5;",
   "nano": {
    "sql": "SELECT origin_code, destination_code, distance_km FROM routes ORDER BY distance_km DESC LIMIT 5;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT origin_code, destination_code, distance_km FROM routes ORDER BY distance_km DESC LIMIT 5;",
    "right": true
   },
   "base": {
    "sql": "SELECT origin_code, destination_code, distance_km\\nFROM routes\\nORDER BY distance_km DESC\\nLIMIT 5;",
    "right": true
   }
  },
  {
   "exam": "exam1",
   "i": 107,
   "domain": "airline operations",
   "difficulty": "simple",
   "question": "Find all crew members whose license expires in 2025, ordered by expiry date.",
   "reference": "SELECT first_name, last_name, role, license_expiry FROM crew WHERE license_expiry LIKE '2025-%' ORDER BY license_expiry;",
   "nano": {
    "sql": "SELECT first_name, last_name, role, license_expiry FROM crew WHERE strftime('%Y', REPLACE(license_expiry, '/', '-')) = '2025' ORDER BY date(REPLACE(license_expiry, '/', '-'));",
    "right": true
   },
   "ultra": {
    "sql": "SELECT crew_id, employee_number, first_name, last_name, role, license_expiry\\nFROM crew\\nWHERE strftime('%Y', REPLACE(license_expiry, '/', '-')) = '2025'\\nORDER BY date(REPLACE(license_expiry, '/', '-'));",
    "right": false
   },
   "base": {
    "sql": "SELECT * FROM crew WHERE license_expiry LIKE '%2025%' ORDER BY license_expiry;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 120,
   "domain": "pharmacy inventory",
   "difficulty": "aggregate",
   "question": "Calculate the total inventory value (quantity on hand * unit cost at purchase) for each store location, sorted by highest value first.",
   "reference": "SELECT sl.location_name, SUM(si.quantity_on_hand * si.unit_cost_at_purchase) AS total_inventory_value FROM store_inventory si JOIN store_locations sl ON si.location_id = sl.location_id WHERE si.unit_cost_at_purchase IS NOT NULL GROUP BY sl.location_id, sl.location_name ORDER BY total_inventory_value DESC;",
   "nano": {
    "sql": "SELECT sl.location_name, SUM(si.quantity_on_hand * si.unit_cost_at_purchase) AS total_inventory_value FROM store_locations sl JOIN store_inventory si ON sl.location_id = si.location_id GROUP BY sl.location_id, sl.location_name ORDER BY total_inventory_value DESC;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT\\n    sl.location_id,\\n    sl.location_name,\\n    SUM(si.quantity_on_hand * si.unit_cost_at_purchase) AS total_inventory_value\\nFROM store_inventory si\\nJOIN store_locations sl ON si.location_id = sl.location_id\\nWHERE si.unit_cost_at_purchase IS NOT NULL\\nGROUP BY sl.location_id, sl.location_name\\nORDER BY total_inventory_value DESC;",
    "right": false
   },
   "base": {
    "sql": "SELECT \\n    sl.location_id,\\n    sl.location_name,\\n    SUM(si.quantity_on_hand * si.unit_cost_at_purchase) AS total_inventory_value\\nFROM \\n    store_locations sl\\nJOIN \\n    store_inventory si ON sl.location_id = si.location_id\\nGROUP BY \\n    sl.location_id, sl.location_name\\nORDER BY \\n    total_inventory_value DESC;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 20,
   "domain": "hotel reservations",
   "difficulty": "simple",
   "question": "List the five most recently registered guests.",
   "reference": "SELECT guest_id, full_name, email, loyalty_tier, registration_date FROM guest_profiles ORDER BY registration_date DESC LIMIT 5;",
   "nano": {
    "sql": "SELECT full_name, email, registration_date FROM guest_profiles ORDER BY date(REPLACE(registration_date, '/', '-')) DESC LIMIT 5;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT guest_id, full_name, email, phone, nationality, loyalty_tier, registration_date\\nFROM guest_profiles\\nORDER BY date(REPLACE(registration_date, '/', '-')) DESC\\nLIMIT 5;",
    "right": false
   },
   "base": {
    "sql": "SELECT full_name, registration_date \\nFROM guest_profiles \\nORDER BY registration_date DESC \\nLIMIT 5;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 22,
   "domain": "hotel reservations",
   "difficulty": "join",
   "question": "For every booking that has been checked out, show the guest name, the room category, and the branch city.",
   "reference": "SELECT gp.full_name, rc.category_name, b.city\\nFROM bookings bk\\nJOIN guest_profiles gp ON bk.guest_id = gp.guest_id\\nJOIN accommodations a ON bk.room_id = a.room_id\\nJOIN room_categories rc ON a.category_id = rc.category_id\\nJOIN branches b ON a.branch_id = b.branch_id\\nWHERE bk.booking_status = 'CheckedOut'\\nORDER BY bk.check_out_date;",
   "nano": {
    "sql": "SELECT gp.full_name, rc.category_name, b.city FROM bookings b JOIN guest_profiles gp ON b.guest_id = gp.guest_id JOIN accommodations a ON b.room_id = a.room_id JOIN room_categories rc ON a.category_id = rc.category_id JOIN branches bn ON a.branch_id = bn.branch_id WHERE b.booking_status = 'CheckedOut';",
    "right": false
   },
   "ultra": {
    "sql": "SELECT gp.full_name, rc.category_name, b.city\\nFROM bookings bk\\nJOIN guest_profiles gp ON bk.guest_id = gp.guest_id\\nJOIN accommodations a ON bk.room_id = a.room_id\\nJOIN room_categories rc ON a.category_id = rc.category_id\\nJOIN branches b ON a.branch_id = b.branch_id\\nWHERE bk.booking_status = 'CheckedOut';",
    "right": false
   },
   "base": {
    "sql": "SELECT gp.full_name, ac.category_name, b.city\\nFROM bookings b\\nJOIN guest_profiles gp ON b.guest_id = gp.guest_id\\nJOIN accommodations ac ON b.room_id = ac.room_id\\nJOIN branches b ON ac.branch_id = b.branch_id\\nWHERE b.booking_status = 'CheckedOut';",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 36,
   "domain": "clinic appointments",
   "difficulty": "hard",
   "question": "For each provider, show their total completed visits and total copay collected, but only for providers with more than 1 completed visit.",
   "reference": "SELECT cp.first_name || ' ' || cp.last_name AS provider_name, cp.medical_specialty, COUNT(sv.visit_id) AS completed_visits, SUM(sv.copay_amount) AS total_copay FROM care_providers cp JOIN scheduled_visits sv ON cp.provider_id = sv.provider_id WHERE sv.visit_status = 'Completed' GROUP BY cp.provider_id, provider_name, cp.medical_specialty HAVING completed_visits > 1 ORDER BY total_copay DESC;",
   "nano": {
    "sql": "SELECT cp.first_name || ' ' || cp.last_name AS provider_name, COUNT(sv.visit_id) AS completed_visits, SUM(sv.copay_amount) AS total_copay FROM care_providers cp JOIN scheduled_visits sv ON cp.provider_id = sv.provider_id WHERE sv.visit_status = 'Completed' GROUP BY cp.provider_id, provider_name HAVING COUNT(sv.visit_id) > 1 ORDER BY total_copay DESC;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT\\n    cp.provider_id,\\n    cp.first_name,\\n    cp.last_name,\\n    COUNT(sv.visit_id) AS total_completed_visits,\\n    SUM(sv.copay_amount) AS total_copay_collected\\nFROM care_providers cp\\nJOIN scheduled_visits sv ON cp.provider_id = sv.provider_id\\nWHERE sv.visit_status = 'Completed'\\nGROUP BY cp.provider_id, cp.first_name, cp.last_name\\nHAVING COUNT(sv.visit_id) > 1;",
    "right": false
   },
   "base": {
    "sql": "SELECT provider_id, COUNT(*) AS total_completed_visits, SUM(copay_amount) AS total_copay_collected\\nFROM scheduled_visits\\nWHERE visit_status = 'Completed'\\nGROUP BY provider_id\\nHAVING COUNT(*) > 1;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 59,
   "domain": "video game studio",
   "difficulty": "simple",
   "question": "Show the 5 largest asset files by size, including the asset name, type, and project ID.",
   "reference": "SELECT asset_name, asset_type, project_id, file_size_mb FROM asset ORDER BY file_size_mb DESC LIMIT 5;",
   "nano": {
    "sql": "SELECT asset_name, asset_type, file_size_mb FROM asset ORDER BY file_size_mb DESC LIMIT 5;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT asset_name, asset_type, project_id, file_size_mb\\nFROM asset\\nORDER BY file_size_mb DESC\\nLIMIT 5;",
    "right": true
   },
   "base": {
    "sql": "SELECT asset_name, asset_type, project_id\\nFROM asset\\nORDER BY file_size_mb DESC\\nLIMIT 5;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 64,
   "domain": "video game studio",
   "difficulty": "aggregate",
   "question": "How many active studio members are assigned to each project, and what is the average hourly rate for those members?",
   "reference": "SELECT gp.title, COUNT(sm.member_id) AS active_member_count, ROUND(AVG(sm.hourly_rate_usd), 2) AS avg_hourly_rate FROM game_project gp LEFT JOIN studio_member sm ON gp.project_id = sm.project_id AND sm.is_active = 1 GROUP BY gp.project_id, gp.title ORDER BY active_member_count DESC;",
   "nano": {
    "sql": "SELECT gp.title, COUNT(sm.member_id) AS active_member_count, ROUND(AVG(sm.hourly_rate_usd), 2) AS avg_hourly_rate FROM game_project gp LEFT JOIN studio_member sm ON gp.project_id = sm.project_id AND sm.is_active = 1 GROUP BY gp.project_id, gp.title ORDER BY gp.title;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT\\n    gp.project_id,\\n    gp.title,\\n    COUNT(sm.member_id) AS active_member_count,\\n    AVG(sm.hourly_rate_usd) AS average_hourly_rate\\nFROM game_project gp\\nLEFT JOIN studio_member sm\\n    ON sm.project_id = gp.project_id\\n    AND sm.is_active = 1\\nGROUP BY gp.project_id, gp.title;",
    "right": false
   },
   "base": {
    "sql": "SELECT project_id, COUNT(*) AS active_member_count, AVG(hourly_rate_usd) AS avg_hourly_rate\\nFROM studio_member\\nWHERE is_active = 1\\nGROUP BY project_id;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 67,
   "domain": "pharmacy inventory",
   "difficulty": "simple",
   "question": "Show the 5 most recent inventory batches received, including their lot numbers and expiry dates.",
   "reference": "SELECT batch_id, lot_number, received_date, expiry_date, quantity_received FROM inventory_batches ORDER BY date(REPLACE(received_date, '/', '-')) DESC LIMIT 5;",
   "nano": {
    "sql": "SELECT batch_id, lot_number, expiry_date FROM inventory_batches ORDER BY date(REPLACE(received_date, '/', '-')) DESC LIMIT 5;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT lot_number, expiry_date\\nFROM inventory_batches\\nORDER BY received_date DESC\\nLIMIT 5;",
    "right": false
   },
   "base": {
    "sql": "SELECT batch_id, lot_number, expiry_date \\nFROM inventory_batches \\nORDER BY received_date DESC \\nLIMIT 5;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 71,
   "domain": "pharmacy inventory",
   "difficulty": "join",
   "question": "Show each sales transaction with the medication name, lot number, and supplier name.",
   "reference": "SELECT st.transaction_id, m.generic_name, m.brand_name, ib.lot_number, s.supplier_name, st.quantity_sold, st.sale_price_per_unit, st.transaction_date FROM sales_transactions st JOIN inventory_batches ib ON st.batch_id = ib.batch_id JOIN medications m ON ib.medication_id = m.medication_id JOIN suppliers s ON ib.supplier_id = s.supplier_id ORDER BY date(REPLACE(st.transaction_date, '/', '-')) DESC;",
   "nano": {
    "sql": "SELECT st.transaction_id, m.generic_name, ib.lot_number, s.supplier_name, st.quantity_sold, st.sale_price_per_unit, st.transaction_date FROM sales_transactions st JOIN inventory_batches ib ON st.batch_id = ib.batch_id JOIN medications m ON ib.medication_id = m.medication_id JOIN suppliers s ON ib.supplier_id = s.supplier_id ORDER BY st.transaction_date;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT\\n    st.transaction_id,\\n    m.generic_name AS medication_name,\\n    ib.lot_number,\\n    s.supplier_name\\nFROM sales_transactions st\\nJOIN inventory_batches ib ON st.batch_id = ib.batch_id\\nJOIN medications m ON ib.medication_id = m.medication_id\\nJOIN suppliers s ON ib.supplier_id = s.supplier_id;",
    "right": false
   },
   "base": {
    "sql": "SELECT s.supplier_name, i.lot_number, m.generic_name, st.transaction_id\\nFROM sales_transactions st\\nJOIN inventory_batches i ON st.batch_id = i.batch_id\\nJOIN medications m ON i.medication_id = m.medication_id\\nJOIN suppliers s ON i.supplier_id = s.supplier_id;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 102,
   "domain": "event ticketing",
   "difficulty": "aggregate",
   "question": "How many tickets of each type have been sold for the Quantum Computing Seminar?",
   "reference": "SELECT tt.name, SUM(p.quantity) AS tickets_sold FROM ticket_type tt JOIN purchase p ON tt.ticket_type_id = p.ticket_type_id WHERE tt.event_id = 3 AND p.status = 'Confirmed' GROUP BY tt.ticket_type_id, tt.name;",
   "nano": {
    "sql": "SELECT tt.name AS ticket_type, tt.quantity_sold FROM ticket_type tt JOIN event e ON tt.event_id = e.event_id WHERE e.title = 'Quantum Computing Seminar' ORDER BY tt.quantity_sold DESC;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT tt.name, SUM(p.quantity) AS tickets_sold\\nFROM ticket_type tt\\nJOIN event e ON tt.event_id = e.event_id\\nJOIN purchase p ON tt.ticket_type_id = p.ticket_type_id\\nWHERE e.title = 'Quantum Computing Seminar'\\n  AND p.status = 'Confirmed'\\nGROUP BY tt.ticket_type_id, tt.name;",
    "right": true
   },
   "base": {
    "sql": "SELECT tt.name, tt.quantity_sold\\nFROM ticket_type tt\\nJOIN event e ON tt.event_id = e.event_id\\nWHERE e.title = 'Quantum Computing Seminar';",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 125,
   "domain": "software subscriptions (SaaS)",
   "difficulty": "simple",
   "question": "Which customer accounts are based in the United States?",
   "reference": "SELECT company_name, primary_contact_email, account_tier FROM customer_accounts WHERE billing_country = 'US';",
   "nano": {
    "sql": "SELECT company_name, industry_sector, primary_contact_email FROM customer_accounts WHERE billing_country = 'US';",
    "right": false
   },
   "ultra": {
    "sql": "SELECT account_id, company_name, industry_sector, primary_contact_email, billing_country, account_tier, created_at, is_active FROM customer_accounts WHERE billing_country = 'United States';",
    "right": false
   },
   "base": {
    "sql": "SELECT DISTINCT account_id, company_name, billing_country\\nFROM customer_accounts\\nWHERE billing_country = 'United States';",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 130,
   "domain": "software subscriptions (SaaS)",
   "difficulty": "aggregate",
   "question": "How many active subscriptions does each company have, and what is the total number of provisioned seats per company?",
   "reference": "SELECT ca.company_name, COUNT(asub.subscription_id) AS subscription_count, SUM(asub.provisioned_seats) AS total_seats FROM active_subscriptions asub JOIN customer_accounts ca ON asub.account_id = ca.account_id WHERE asub.status = 'Active' GROUP BY ca.company_name;",
   "nano": {
    "sql": "SELECT ca.company_name, COUNT(asub.subscription_id) AS active_subscription_count, SUM(asub.provisioned_seats) AS total_provisioned_seats FROM customer_accounts ca LEFT JOIN active_subscriptions asub ON ca.account_id = asub.account_id AND asub.status = 'Active' GROUP BY ca.company_name ORDER BY active_subscription_count DESC;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT\\n    ca.company_name,\\n    COUNT(asub.subscription_id) AS active_subscription_count,\\n    SUM(asub.provisioned_seats) AS total_provisioned_seats\\nFROM\\n    customer_accounts ca\\nJOIN\\n    active_subscriptions asub ON ca.account_id = asub.account_id\\nWHERE\\n    asub.status = 'Active'\\nGROUP BY\\n    ca.company_name;",
    "right": true
   },
   "base": {
    "sql": "SELECT \\n    ca.company_name,\\n    COUNT(as.subscription_id) AS active_subscriptions,\\n    SUM(as.provisioned_seats) AS total_provisioned_seats\\nFROM \\n    customer_accounts ca\\nJOIN \\n    active_subscriptions as ON ca.account_id = as.account_id\\nGROUP BY \\n    ca.company_name;",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 143,
   "domain": "telecom plans",
   "difficulty": "simple",
   "question": "List all network towers that are currently marked as Active.",
   "reference": "SELECT tower_code, tower_type, status FROM network_towers WHERE status = 'Active';",
   "nano": {
    "sql": "SELECT tower_code, region_id, tower_type, installation_date FROM network_towers WHERE status = 'Active' ORDER BY installation_date;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT * FROM network_towers WHERE status = 'Active';",
    "right": false
   },
   "base": {
    "sql": "SELECT * FROM network_towers WHERE status = 'Active';",
    "right": false
   }
  },
  {
   "exam": "exam1",
   "i": 151,
   "domain": "telecom plans",
   "difficulty": "hard",
   "question": "List customers who have a 'Family Share' plan and have at least one 5G data session recorded.",
   "reference": "SELECT DISTINCT ca.full_name, st.tier_name FROM customer_accounts ca JOIN subscription_tiers st ON ca.tier_id = st.tier_id JOIN usage_events ue ON ca.account_id = ue.account_id WHERE st.tier_name LIKE 'Family Share%' AND ue.session_type = 'Data' AND ue.technology LIKE '5G%';",
   "nano": {
    "sql": "SELECT DISTINCT ca.full_name, ca.account_number FROM customer_accounts ca JOIN subscription_tiers st ON ca.tier_id = st.tier_id JOIN usage_events ue ON ca.account_id = ue.account_id WHERE st.tier_name = 'Family Share' AND ue.technology = '5G NR' AND ue.session_type = 'Data';",
    "right": false
   },
   "ultra": {
    "sql": "SELECT DISTINCT ca.account_id, ca.account_number, ca.full_name, ca.email\\nFROM customer_accounts ca\\nJOIN subscription_tiers st ON ca.tier_id = st.tier_id\\nJOIN usage_events ue ON ca.account_id = ue.account_id\\nWHERE st.tier_name = 'Family Share'\\n  AND ue.session_type = 'Data'\\n  AND ue.technology IN ('5G NR', '5G NSA');",
    "right": false
   },
   "base": {
    "sql": "SELECT DISTINCT c.full_name, c.account_number, c.region_name\\nFROM customer_accounts c\\nJOIN subscription_tiers t ON c.tier_id = t.tier_id\\nJOIN usage_events u ON c.account_id = u.account_id\\nJOIN service_regions r ON c.region_id = r.region_id\\nWHERE t.tier_name = 'Family Share'\\n  AND u.session_type = 'Data'\\n  AND u.technology = '5G NR';",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 2,
   "domain": "museum collections",
   "difficulty": "simple",
   "question": "List the rooms on the second floor from largest to smallest, with their sizes.",
   "reference": "SELECT name, area_sqm FROM galleries WHERE floor = 2 ORDER BY area_sqm DESC;",
   "nano": {
    "sql": "SELECT name, area_sqm FROM galleries WHERE floor = 2 ORDER BY area_sqm DESC;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT name, area_sqm FROM galleries WHERE floor = 2 ORDER BY area_sqm DESC;",
    "right": true
   },
   "base": {
    "sql": "SELECT name, area_sqm FROM galleries WHERE floor = 2 ORDER BY area_sqm DESC;",
    "right": true
   }
  },
  {
   "exam": "exam2",
   "i": 8,
   "domain": "museum collections",
   "difficulty": "join",
   "question": "Which pieces are not currently assigned to any room, and who are the artists?",
   "reference": "SELECT aw.title, ar.full_name FROM artworks aw JOIN artists ar ON ar.artist_id = aw.artist_id WHERE aw.gallery_id IS NULL;",
   "nano": {
    "sql": "SELECT a.title, ar.full_name FROM artworks a JOIN artists ar ON a.artist_id = ar.artist_id WHERE a.gallery_id IS NULL;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT a.title, ar.full_name\\nFROM artworks a\\nJOIN artists ar ON a.artist_id = ar.artist_id\\nWHERE a.gallery_id IS NULL;",
    "right": true
   },
   "base": {
    "sql": "SELECT a.full_name, a.birth_year, a.death_year\\nFROM artworks a\\nLEFT JOIN exhibitions e ON a.gallery_id = e.gallery_id AND e.end_date IS NOT NULL\\nLEFT JOIN exhibition_artworks ea ON a.artwork_id = ea.artwork_id AND ea.exhibition_id IS NOT NULL\\nWHERE a.gallery_id IS NULL OR e.end_date IS NULL OR ea.exhibition_id IS NULL\\nGROUP BY a.artwork_id, a.full_name, a.birth_year, a.death_year;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 11,
   "domain": "museum collections",
   "difficulty": "join",
   "question": "Which exhibitions have no artworks recorded against them yet?",
   "reference": "SELECT e.title, e.start_date FROM exhibitions e LEFT JOIN exhibition_artworks ea ON ea.exhibition_id = e.exhibition_id WHERE ea.artwork_id IS NULL;",
   "nano": {
    "sql": "SELECT e.title, e.start_date FROM exhibitions e WHERE NOT EXISTS (SELECT 1 FROM exhibition_artworks ea WHERE ea.exhibition_id = e.exhibition_id) ORDER BY e.start_date;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT e.exhibition_id, e.title\\nFROM exhibitions e\\nLEFT JOIN exhibition_artworks ea ON e.exhibition_id = ea.exhibition_id\\nWHERE ea.exhibition_id IS NULL;",
    "right": false
   },
   "base": {
    "sql": "SELECT e.exhibition_id, e.title, e.start_date, e.end_date, e.gallery_id, e.ticket_price\\nFROM exhibitions e\\nLEFT JOIN exhibition_artworks ea ON e.exhibition_id = ea.exhibition_id\\nWHERE ea.exhibition_id IS NULL;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 17,
   "domain": "museum collections",
   "difficulty": "hard",
   "question": "Which artists have more than one piece in our collection, and how many do we have?",
   "reference": "SELECT ar.full_name, COUNT(*) AS works_held FROM artists ar JOIN artworks aw ON aw.artist_id = ar.artist_id GROUP BY ar.artist_id, ar.full_name HAVING COUNT(*) > 1 ORDER BY works_held DESC, ar.full_name;",
   "nano": {
    "sql": "SELECT ar.full_name, COUNT(a.artwork_id) AS piece_count FROM artists ar JOIN artworks a ON ar.artist_id = a.artist_id GROUP BY ar.artist_id, ar.full_name HAVING COUNT(a.artwork_id) > 1 ORDER BY piece_count DESC;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT a.full_name, COUNT(aw.artwork_id) AS artwork_count\\nFROM artists a\\nJOIN artworks aw ON a.artist_id = aw.artist_id\\nGROUP BY a.artist_id, a.full_name\\nHAVING COUNT(aw.artwork_id) > 1\\nORDER BY artwork_count DESC;",
    "right": true
   },
   "base": {
    "sql": "SELECT a.full_name, COUNT(*) AS piece_count\\nFROM artists a\\nJOIN artworks w ON a.artist_id = w.artist_id\\nGROUP BY a.artist_id, a.full_name\\nHAVING COUNT(*) > 1;",
    "right": true
   }
  },
  {
   "exam": "exam2",
   "i": 45,
   "domain": "wine distribution",
   "difficulty": "simple",
   "question": "What white wines from the 2022 vintage do we carry?",
   "reference": "SELECT wine_name, varietal, bottle_price FROM wines WHERE color = 'White' AND vintage = 2022 ORDER BY wine_name;",
   "nano": {
    "sql": "SELECT wine_name, varietal, bottle_price FROM wines WHERE color = 'White' AND vintage = 2022 ORDER BY bottle_price;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT wine_name, varietal, vintage, bottle_price\\nFROM wines\\nWHERE color = 'White' AND vintage = 2022;",
    "right": false
   },
   "base": {
    "sql": "SELECT DISTINCT w.wine_name, w.varietal, w.color, w.bottle_price, w.alcohol_pct\\nFROM wines w\\nJOIN producers p ON w.producer_id = p.producer_id\\nWHERE w.color = 'White' AND w.vintage = 2022;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 74,
   "domain": "film production schedule",
   "difficulty": "aggregate",
   "question": "For each genre, how many films do we have and what is their combined budget?",
   "reference": "SELECT genre, COUNT(*) AS film_count, SUM(budget_usd) AS total_budget FROM productions GROUP BY genre ORDER BY total_budget DESC;",
   "nano": {
    "sql": "SELECT genre, COUNT(*) AS film_count, SUM(budget_usd) AS total_budget FROM productions GROUP BY genre ORDER BY total_budget DESC;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT genre, COUNT(*) AS film_count, SUM(budget_usd) AS total_budget FROM productions GROUP BY genre;",
    "right": false
   },
   "base": {
    "sql": "SELECT genre, COUNT(*) AS film_count, SUM(budget_usd) AS total_budget\\nFROM productions\\nGROUP BY genre;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 82,
   "domain": "wind farm maintenance",
   "difficulty": "simple",
   "question": "Which maintenance jobs are still open, oldest first?",
   "reference": "SELECT work_order_id, order_type, priority, opened_date FROM work_orders WHERE completed_date IS NULL ORDER BY opened_date ASC;",
   "nano": {
    "sql": "SELECT work_order_id, order_type, priority, opened_date FROM work_orders WHERE completed_date IS NULL ORDER BY opened_date;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT\\n    wo.work_order_id,\\n    wo.turbine_id,\\n    wo.tech_id,\\n    wo.order_type,\\n    wo.priority,\\n    wo.opened_date,\\n    wo.labor_hours,\\n    wo.parts_cost,\\n    wo.description\\nFROM work_orders wo\\nWHERE wo.completed_date IS NULL\\nORDER BY date(REPLACE(wo.opened_date, '/', '-')) ASC;",
    "right": false
   },
   "base": {
    "sql": "SELECT * FROM work_orders WHERE completed_date IS NULL ORDER BY opened_date;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 104,
   "domain": "court case management",
   "difficulty": "simple",
   "question": "Which judges have no area of specialty on record, and what courtroom are they in?",
   "reference": "SELECT full_name, courtroom FROM judges WHERE specialty IS NULL;",
   "nano": {
    "sql": "SELECT full_name, courtroom FROM judges WHERE specialty IS NULL;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT full_name, courtroom FROM judges WHERE specialty IS NULL OR specialty = '';",
    "right": true
   },
   "base": {
    "sql": "SELECT j.full_name, j.courtroom\\nFROM judges j\\nWHERE j.specialty IS NULL;",
    "right": true
   }
  },
  {
   "exam": "exam2",
   "i": 132,
   "domain": "recycling plant operations",
   "difficulty": "aggregate",
   "question": "How many kilograms have we received in total for each type of material category?",
   "reference": "SELECT m.category, SUM(d.weight_kg) AS total_kg FROM deliveries d JOIN materials m ON m.material_id = d.material_id GROUP BY m.category ORDER BY total_kg DESC;",
   "nano": {
    "sql": "SELECT m.category, SUM(d.weight_kg) AS total_kg FROM deliveries d JOIN materials m ON d.material_id = m.material_id GROUP BY m.category ORDER BY total_kg DESC;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT m.category, SUM(d.weight_kg) AS total_kg\\nFROM deliveries d\\nJOIN materials m ON d.material_id = m.material_id\\nGROUP BY m.category;",
    "right": false
   },
   "base": {
    "sql": "SELECT m.category, SUM(d.weight_kg) AS total_kg_received\\nFROM deliveries d\\nJOIN materials m ON d.material_id = m.material_id\\nGROUP BY m.category;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 165,
   "domain": "cinema chain",
   "difficulty": "simple",
   "question": "Which films opened between April and June 2024, in order of release?",
   "reference": "SELECT title, release_date FROM movies WHERE release_date BETWEEN '2024-04-01' AND '2024-06-30' ORDER BY release_date;",
   "nano": {
    "sql": "SELECT title, release_date FROM movies WHERE strftime('%Y-%m', REPLACE(release_date, '/', '-')) BETWEEN '2024-04' AND '2024-06' ORDER BY release_date;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT title, release_date\\nFROM movies\\nWHERE date(REPLACE(release_date, '/', '-')) BETWEEN '2024-04-01' AND '2024-06-30'\\nORDER BY date(REPLACE(release_date, '/', '-'));",
    "right": true
   },
   "base": {
    "sql": "SELECT title, release_date\\nFROM movies\\nWHERE release_date BETWEEN '2024-04-01' AND '2024-06-30'\\nORDER BY release_date;",
    "right": true
   }
  },
  {
   "exam": "exam2",
   "i": 181,
   "domain": "fishing fleet",
   "difficulty": "simple",
   "question": "Who are our five most experienced captains?",
   "reference": "SELECT full_name, years_experience FROM captains ORDER BY years_experience DESC LIMIT 5;",
   "nano": {
    "sql": "SELECT full_name, years_experience FROM captains ORDER BY years_experience DESC LIMIT 5;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT full_name, years_experience FROM captains ORDER BY years_experience DESC LIMIT 5;",
    "right": true
   },
   "base": {
    "sql": "SELECT full_name, years_experience \\nFROM captains \\nORDER BY years_experience DESC \\nLIMIT 5;",
    "right": true
   }
  },
  {
   "exam": "exam2",
   "i": 192,
   "domain": "fishing fleet",
   "difficulty": "aggregate",
   "question": "How many kilos of each species have we caught in total, biggest first?",
   "reference": "SELECT species, SUM(weight_kg) AS total_kg FROM catches GROUP BY species ORDER BY total_kg DESC;",
   "nano": {
    "sql": "SELECT species, SUM(weight_kg) AS total_kg FROM catches GROUP BY species ORDER BY total_kg DESC;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT species, SUM(weight_kg) AS total_kg FROM catches GROUP BY species ORDER BY total_kg DESC;",
    "right": true
   },
   "base": {
    "sql": "SELECT species, SUM(weight_kg) AS total_kg\\nFROM catches\\nGROUP BY species\\nORDER BY total_kg DESC;",
    "right": true
   }
  },
  {
   "exam": "exam2",
   "i": 194,
   "domain": "fishing fleet",
   "difficulty": "aggregate",
   "question": "What is the average fuel use per trip in each fishing area?",
   "reference": "SELECT fishing_area, AVG(fuel_liters) AS avg_fuel_liters FROM trips GROUP BY fishing_area ORDER BY avg_fuel_liters DESC;",
   "nano": {
    "sql": "SELECT fishing_area, AVG(fuel_liters) AS avg_fuel_liters FROM trips GROUP BY fishing_area ORDER BY avg_fuel_liters DESC;",
    "right": true
   },
   "ultra": {
    "sql": "SELECT fishing_area, AVG(fuel_liters) AS avg_fuel_per_trip\\nFROM trips\\nGROUP BY fishing_area;",
    "right": false
   },
   "base": {
    "sql": "SELECT fishing_area, AVG(fuel_liters) AS avg_fuel_liters\\nFROM trips\\nGROUP BY fishing_area;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 19,
   "domain": "museum collections",
   "difficulty": "hard",
   "question": "Among exhibitions that have already ended, which five ran the longest, and how many days did each last?",
   "reference": "SELECT title, CAST(julianday(end_date) - julianday(start_date) AS INTEGER) AS days_open FROM exhibitions WHERE end_date IS NOT NULL ORDER BY days_open DESC LIMIT 5;",
   "nano": {
    "sql": "SELECT title, start_date, end_date, CAST(julianday(end_date) - julianday(start_date) AS INTEGER) AS duration_days FROM exhibitions WHERE end_date IS NOT NULL ORDER BY duration_days DESC LIMIT 5;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT e.title,\\n       CAST(julianday(e.end_date) - julianday(e.start_date) AS INTEGER) AS duration_days\\nFROM exhibitions e\\nWHERE e.end_date IS NOT NULL\\n  AND date(REPLACE(e.end_date, '/', '-')) < date('now')\\nORDER BY duration_days DESC\\nLIMIT 5;",
    "right": true
   },
   "base": {
    "sql": "SELECT e.title, e.gallery_id, \\n       (strftime('%s', e.start_date) - strftime('%s', e.end_date)) / 86400 AS days_last\\nFROM exhibitions e\\nWHERE e.end_date IS NOT NULL\\nORDER BY days_last DESC\\nLIMIT 5;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 27,
   "domain": "veterinary clinic",
   "difficulty": "join",
   "question": "Which clients own cats, and what are the cats called?",
   "reference": "SELECT o.first_name, o.last_name, p.name AS cat_name FROM owners o JOIN pets p ON p.owner_id = o.owner_id WHERE p.species = 'Cat' ORDER BY o.last_name, p.name;",
   "nano": {
    "sql": "SELECT o.first_name || ' ' || o.last_name AS owner_name, p.name AS cat_name FROM owners o JOIN pets p ON o.owner_id = p.owner_id WHERE p.species = 'Cat' ORDER BY o.last_name;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT o.first_name, o.last_name, p.name\\nFROM owners o\\nJOIN pets p ON o.owner_id = p.owner_id\\nWHERE p.species = 'cat';",
    "right": false
   },
   "base": {
    "sql": "SELECT o.first_name, o.last_name, p.name\\nFROM owners o\\nJOIN pets p ON o.owner_id = p.owner_id\\nWHERE p.species = 'cat';",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 81,
   "domain": "wind farm maintenance",
   "difficulty": "simple",
   "question": "Who are our five best-paid technicians?",
   "reference": "SELECT full_name, hourly_rate FROM technicians ORDER BY hourly_rate DESC LIMIT 5;",
   "nano": {
    "sql": "SELECT full_name, certification_level, hourly_rate FROM technicians ORDER BY hourly_rate DESC LIMIT 5;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT tech_id, full_name, certification_level, hire_date, hourly_rate, home_base FROM technicians ORDER BY hourly_rate DESC LIMIT 5;",
    "right": false
   },
   "base": {
    "sql": "SELECT full_name, hourly_rate\\nFROM technicians\\nORDER BY hourly_rate DESC\\nLIMIT 5;",
    "right": true
   }
  },
  {
   "exam": "exam2",
   "i": 83,
   "domain": "wind farm maintenance",
   "difficulty": "simple",
   "question": "Show the wind farms in Texas or Wyoming, biggest capacity first.",
   "reference": "SELECT farm_name, state, capacity_mw FROM wind_farms WHERE state IN ('Texas', 'Wyoming') ORDER BY capacity_mw DESC;",
   "nano": {
    "sql": "SELECT farm_name, state, capacity_mw FROM wind_farms WHERE state IN ('TX', 'WY') ORDER BY capacity_mw DESC;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT farm_id, farm_name, state, commissioned_date, capacity_mw, operator\\nFROM wind_farms\\nWHERE state IN ('Texas', 'Wyoming')\\nORDER BY capacity_mw DESC;",
    "right": false
   },
   "base": {
    "sql": "SELECT farm_name, capacity_mw, state\\nFROM wind_farms\\nWHERE state IN ('Texas', 'Wyoming')\\nORDER BY capacity_mw DESC;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 99,
   "domain": "wind farm maintenance",
   "difficulty": "hard",
   "question": "Which technicians earn more per hour than the average technician?",
   "reference": "SELECT full_name, hourly_rate FROM technicians WHERE hourly_rate > (SELECT AVG(hourly_rate) FROM technicians) ORDER BY hourly_rate DESC;",
   "nano": {
    "sql": "SELECT full_name, certification_level, hourly_rate FROM technicians WHERE hourly_rate > (SELECT AVG(hourly_rate) FROM technicians) ORDER BY hourly_rate DESC;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT tech_id, full_name, hourly_rate\\nFROM technicians\\nWHERE hourly_rate > (SELECT AVG(hourly_rate) FROM technicians);",
    "right": false
   },
   "base": {
    "sql": "SELECT t.full_name, t.hourly_rate\\nFROM technicians t\\nWHERE t.hourly_rate > (SELECT AVG(hourly_rate) FROM technicians);",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 125,
   "domain": "recycling plant operations",
   "difficulty": "simple",
   "question": "Which sales have not been confirmed as paid yet?",
   "reference": "SELECT sale_id, buyer_name, sale_date, total_revenue, payment_status FROM sales WHERE payment_status IS NULL OR payment_status <> 'Paid' ORDER BY sale_date;",
   "nano": {
    "sql": "SELECT sale_id, material_id, buyer_name, sale_date, weight_kg, total_revenue, payment_status FROM sales WHERE payment_status != 'Paid' ORDER BY sale_date;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT * FROM sales WHERE payment_status IS NULL OR payment_status = '' OR payment_status != 'Paid';",
    "right": false
   },
   "base": {
    "sql": "SELECT * FROM sales WHERE payment_status IS NULL;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 142,
   "domain": "podcast network",
   "difficulty": "simple",
   "question": "Which of our hosts are based in the USA, and who charges the most per hour?",
   "reference": "SELECT full_name, hourly_rate FROM hosts WHERE country = 'USA' ORDER BY hourly_rate DESC;",
   "nano": {
    "sql": "SELECT full_name, country, hourly_rate FROM hosts WHERE country = 'USA' ORDER BY hourly_rate DESC LIMIT 5;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT full_name, hourly_rate\\nFROM hosts\\nWHERE country = 'USA'\\nORDER BY hourly_rate DESC\\nLIMIT 1;",
    "right": false
   },
   "base": {
    "sql": "SELECT h.full_name, h.hourly_rate\\nFROM hosts h\\nWHERE h.country = 'USA'\\nORDER BY h.hourly_rate DESC;",
    "right": true
   }
  },
  {
   "exam": "exam2",
   "i": 152,
   "domain": "podcast network",
   "difficulty": "aggregate",
   "question": "How many shows do we have in each genre, starting with the biggest genres?",
   "reference": "SELECT genre, COUNT(*) AS show_count FROM shows GROUP BY genre ORDER BY show_count DESC, genre;",
   "nano": {
    "sql": "SELECT genre, COUNT(*) AS show_count FROM shows GROUP BY genre ORDER BY show_count DESC;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT genre, COUNT(*) AS show_count\\nFROM shows\\nGROUP BY genre\\nORDER BY show_count DESC;",
    "right": false
   },
   "base": {
    "sql": "SELECT genre, COUNT(*) AS show_count\\nFROM shows\\nGROUP BY genre\\nORDER BY show_count DESC;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 168,
   "domain": "cinema chain",
   "difficulty": "join",
   "question": "What was shown at our Chicago locations, and what did a ticket cost?",
   "reference": "SELECT c.name AS cinema, m.title, s.start_time, s.ticket_price FROM cinemas c JOIN screenings s ON s.cinema_id = c.cinema_id JOIN movies m ON m.movie_id = s.movie_id WHERE c.city = 'Chicago' ORDER BY s.start_time;",
   "nano": {
    "sql": "SELECT c.name AS cinema_name, m.title AS movie_title, s.ticket_price FROM screenings s JOIN cinemas c ON s.cinema_id = c.cinema_id JOIN movies m ON s.movie_id = m.movie_id WHERE c.city = 'Chicago' ORDER BY s.start_time;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT m.title, s.ticket_price\\nFROM screenings s\\nJOIN cinemas c ON s.cinema_id = c.cinema_id\\nJOIN movies m ON s.movie_id = m.movie_id\\nWHERE c.city = 'Chicago';",
    "right": false
   },
   "base": {
    "sql": "SELECT s.screen_number, s.ticket_price\\nFROM screenings s\\nJOIN cinemas c ON s.cinema_id = c.cinema_id\\nWHERE c.city = 'Chicago';",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 180,
   "domain": "fishing fleet",
   "difficulty": "simple",
   "question": "Which boats are longer than 35 meters, from longest to shortest?",
   "reference": "SELECT name, length_m FROM vessels WHERE length_m > 35 ORDER BY length_m DESC;",
   "nano": {
    "sql": "SELECT name, vessel_type, length_m FROM vessels WHERE length_m > 35 ORDER BY length_m DESC;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT name, length_m FROM vessels WHERE length_m > 35 ORDER BY length_m DESC;",
    "right": true
   },
   "base": {
    "sql": "SELECT name, length_m FROM vessels WHERE length_m > 35 ORDER BY length_m DESC;",
    "right": true
   }
  },
  {
   "exam": "exam2",
   "i": 191,
   "domain": "fishing fleet",
   "difficulty": "join",
   "question": "For the trips currently underway, which boat is out and who is the captain?",
   "reference": "SELECT t.trip_id, v.name AS vessel_name, c.full_name AS captain_name, t.departure_date FROM trips t JOIN vessels v ON v.vessel_id = t.vessel_id JOIN captains c ON c.captain_id = t.captain_id WHERE t.return_date IS NULL;",
   "nano": {
    "sql": "SELECT v.name AS vessel_name, c.full_name AS captain_name, t.departure_date FROM trips t JOIN vessels v ON t.vessel_id = v.vessel_id JOIN captains c ON t.captain_id = c.captain_id WHERE t.return_date IS NULL;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT v.name AS vessel_name, c.full_name AS captain_name\\nFROM trips t\\nJOIN vessels v ON t.vessel_id = v.vessel_id\\nJOIN captains c ON t.captain_id = c.captain_id\\nWHERE t.return_date IS NULL;",
    "right": false
   },
   "base": {
    "sql": "SELECT v.name, c.full_name\\nFROM trips t\\nJOIN vessels v ON t.vessel_id = v.vessel_id\\nJOIN captains c ON t.captain_id = c.captain_id\\nWHERE t.return_date IS NULL;",
    "right": false
   }
  },
  {
   "exam": "exam2",
   "i": 195,
   "domain": "fishing fleet",
   "difficulty": "aggregate",
   "question": "What is the average price per kilo for each quality grade, including catches that have no grade?",
   "reference": "SELECT grade, COUNT(*) AS catch_count, ROUND(AVG(price_per_kg), 2) AS avg_price FROM catches GROUP BY grade;",
   "nano": {
    "sql": "SELECT grade, ROUND(AVG(price_per_kg), 2) AS avg_price_per_kg FROM catches GROUP BY grade ORDER BY grade;",
    "right": false
   },
   "ultra": {
    "sql": "SELECT COALESCE(grade, 'No Grade') AS grade, AVG(price_per_kg) AS avg_price_per_kg\\nFROM catches\\nGROUP BY grade;",
    "right": false
   },
   "base": {
    "sql": "SELECT grade, AVG(price_per_kg) AS avg_price_per_kg\\nFROM catches\\nGROUP BY grade;",
    "right": false
   }
  }
 ]
}
`;export{e as default};