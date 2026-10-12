"""Initial schema: shipments, rates, invoices, lines, audit runs and exceptions

Revision ID: 0001
Revises: (none — this is the baseline)
Create Date: 2026-10-12 16:48:25.805855+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('audit_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('triggered_by', sa.String(length=64), nullable=False),
    sa.Column('invoices_audited', sa.Integer(), nullable=False),
    sa.Column('exceptions_found', sa.Integer(), nullable=False),
    sa.Column('total_billed', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('total_variance', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('contracted_rates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('carrier_scac', sa.String(length=8), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('service_level', sa.String(length=32), nullable=False),
    sa.Column('origin_state', sa.String(length=2), nullable=False),
    sa.Column('dest_state', sa.String(length=2), nullable=False),
    sa.Column('charge_code', sa.String(length=32), nullable=False),
    sa.Column('rate_type', sa.String(length=24), nullable=False),
    sa.Column('base_rate', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('min_weight_lb', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('max_weight_lb', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('min_charge', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('max_charge', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('discount_pct', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('fuel_surcharge_pct', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.Column('expiration_date', sa.Date(), nullable=True),
    sa.Column('contract_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('carrier_scac', 'mode', 'service_level', 'origin_state', 'dest_state', 'charge_code', 'min_weight_lb', 'effective_date', name='uq_contracted_rate_key')
    )
    with op.batch_alter_table('contracted_rates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contracted_rates_carrier_scac'), ['carrier_scac'], unique=False)
        batch_op.create_index(batch_op.f('ix_contracted_rates_charge_code'), ['charge_code'], unique=False)
        batch_op.create_index('ix_rates_lookup', ['carrier_scac', 'mode', 'charge_code'], unique=False)

    op.create_table('shipments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('shipment_id', sa.String(length=64), nullable=False),
    sa.Column('reference_number', sa.String(length=64), nullable=True),
    sa.Column('carrier_scac', sa.String(length=8), nullable=False),
    sa.Column('carrier_name', sa.String(length=128), nullable=True),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('service_level', sa.String(length=32), nullable=False),
    sa.Column('origin_city', sa.String(length=64), nullable=True),
    sa.Column('origin_state', sa.String(length=2), nullable=False),
    sa.Column('origin_zip', sa.String(length=10), nullable=False),
    sa.Column('dest_city', sa.String(length=64), nullable=True),
    sa.Column('dest_state', sa.String(length=2), nullable=False),
    sa.Column('dest_zip', sa.String(length=10), nullable=False),
    sa.Column('distance_mi', sa.Numeric(precision=10, scale=1), nullable=True),
    sa.Column('ship_date', sa.Date(), nullable=False),
    sa.Column('promised_date', sa.Date(), nullable=True),
    sa.Column('delivery_date', sa.Date(), nullable=True),
    sa.Column('weight_lb', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('billable_weight_lb', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('pieces', sa.Integer(), nullable=False),
    sa.Column('freight_class', sa.String(length=8), nullable=True),
    sa.Column('declared_value', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('accessorials', sa.String(length=256), nullable=True),
    sa.Column('customer', sa.String(length=128), nullable=True),
    sa.Column('cost_center', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('shipments', schema=None) as batch_op:
        batch_op.create_index('ix_shipments_carrier_date', ['carrier_scac', 'ship_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_shipments_carrier_scac'), ['carrier_scac'], unique=False)
        batch_op.create_index('ix_shipments_lane', ['origin_state', 'dest_state'], unique=False)
        batch_op.create_index(batch_op.f('ix_shipments_mode'), ['mode'], unique=False)
        batch_op.create_index(batch_op.f('ix_shipments_ship_date'), ['ship_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_shipments_shipment_id'), ['shipment_id'], unique=True)

    op.create_table('carrier_invoices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('invoice_number', sa.String(length=64), nullable=False),
    sa.Column('carrier_scac', sa.String(length=8), nullable=False),
    sa.Column('shipment_ref', sa.String(length=64), nullable=True),
    sa.Column('shipment_id', sa.Integer(), nullable=True),
    sa.Column('invoice_date', sa.Date(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['shipment_id'], ['shipments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('carrier_scac', 'invoice_number', name='uq_invoice_carrier_number')
    )
    with op.batch_alter_table('carrier_invoices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_carrier_invoices_carrier_scac'), ['carrier_scac'], unique=False)
        batch_op.create_index(batch_op.f('ix_carrier_invoices_invoice_date'), ['invoice_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_carrier_invoices_invoice_number'), ['invoice_number'], unique=False)
        batch_op.create_index('ix_invoices_shipment', ['shipment_ref'], unique=False)

    op.create_table('audit_exceptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('invoice_id', sa.Integer(), nullable=True),
    sa.Column('shipment_id', sa.Integer(), nullable=True),
    sa.Column('rule_code', sa.String(length=48), nullable=False),
    sa.Column('rule_name', sa.String(length=128), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('charge_code', sa.String(length=32), nullable=False),
    sa.Column('billed_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('expected_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('variance_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('recommendation', sa.Text(), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['carrier_invoices.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['audit_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['shipment_id'], ['shipments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'invoice_id', 'rule_code', 'charge_code', name='uq_exception_key')
    )
    with op.batch_alter_table('audit_exceptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_exceptions_invoice_id'), ['invoice_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_exceptions_rule_code'), ['rule_code'], unique=False)
        batch_op.create_index('ix_exceptions_rule_status', ['rule_code', 'status'], unique=False)

    op.create_table('invoice_line_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('invoice_id', sa.Integer(), nullable=False),
    sa.Column('line_number', sa.Integer(), nullable=False),
    sa.Column('charge_code', sa.String(length=32), nullable=False),
    sa.Column('description', sa.String(length=256), nullable=True),
    sa.Column('quantity', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('unit', sa.String(length=16), nullable=True),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['carrier_invoices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('invoice_line_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invoice_line_items_charge_code'), ['charge_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_invoice_line_items_invoice_id'), ['invoice_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('invoice_line_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invoice_line_items_invoice_id'))
        batch_op.drop_index(batch_op.f('ix_invoice_line_items_charge_code'))

    op.drop_table('invoice_line_items')
    with op.batch_alter_table('audit_exceptions', schema=None) as batch_op:
        batch_op.drop_index('ix_exceptions_rule_status')
        batch_op.drop_index(batch_op.f('ix_audit_exceptions_rule_code'))
        batch_op.drop_index(batch_op.f('ix_audit_exceptions_invoice_id'))

    op.drop_table('audit_exceptions')
    with op.batch_alter_table('carrier_invoices', schema=None) as batch_op:
        batch_op.drop_index('ix_invoices_shipment')
        batch_op.drop_index(batch_op.f('ix_carrier_invoices_invoice_number'))
        batch_op.drop_index(batch_op.f('ix_carrier_invoices_invoice_date'))
        batch_op.drop_index(batch_op.f('ix_carrier_invoices_carrier_scac'))

    op.drop_table('carrier_invoices')
    with op.batch_alter_table('shipments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_shipments_shipment_id'))
        batch_op.drop_index(batch_op.f('ix_shipments_ship_date'))
        batch_op.drop_index(batch_op.f('ix_shipments_mode'))
        batch_op.drop_index('ix_shipments_lane')
        batch_op.drop_index(batch_op.f('ix_shipments_carrier_scac'))
        batch_op.drop_index('ix_shipments_carrier_date')

    op.drop_table('shipments')
    with op.batch_alter_table('contracted_rates', schema=None) as batch_op:
        batch_op.drop_index('ix_rates_lookup')
        batch_op.drop_index(batch_op.f('ix_contracted_rates_charge_code'))
        batch_op.drop_index(batch_op.f('ix_contracted_rates_carrier_scac'))

    op.drop_table('contracted_rates')
    op.drop_table('audit_runs')
