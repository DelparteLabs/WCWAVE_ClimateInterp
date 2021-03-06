import arcpy
#from arcpy.sa import * #If anything is ever not defined see if it is part of arcpy.as
import glob
import os
import sys
import csv
import traceback
import numpy
##from scipy import stats
try:
    import mysql.connector
    from mysql.connector import errorcode
except ImportError:
    print('No MySQL support.  Use SQLite database or install MySQL')
import sqlite3
import datetime
import json
import shutil
import subprocess

import smtplib
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText

##Checkout needed Extentions
class SpatialLicenseError(Exception):
    pass

class GeostatsLicenseError(Exception):
    pass
    
try:
    if arcpy.CheckExtension('Spatial') == "Available":
        arcpy.CheckOutExtension('Spatial')
    else:
        raise SpatialLicenseError
except SpatialLicenseError:
    arcpy.AddError("Spatial License is unavailable")
    sys.exit(1) ## Terminate script
    
try:
    if arcpy.CheckExtension('Geostats') == "Available":
        arcpy.CheckOutExtension('Geostats')
    else:
        raise GeostatsLicenseError
except GeostatsLicenseError:
    arcpy.AddError("Geostats License is unavailable")
    sys.exit(1) ## Terminate script

data = {'sql_ph': '%s'}

##Set up workspaces
scratchWS = arcpy.env.scratchFolder
arcpy.env.workspace = scratchWS
arcpy.env.scratchWorkspace = scratchWS
arcpy.AddMessage('Scratch Workspace: ' + scratchWS)
scratchGDB = arcpy.env.scratchGDB
arcpy.env.overwriteOutput = True

##Add workspace to data dict
data['scratch_ws'] = scratchWS
data['scratch_gdb'] = scratchGDB

date_now = datetime.datetime.now()
s_now = date_now.strftime('%Y%d%b_%H%M%S')
os.makedirs(scratchWS + '/Output_' + s_now)
outFolder = '{0}/Output_{1}'.format(scratchWS, s_now)
data['out_folder'] = outFolder
##arcpy.AddMessage('Output Folder: ' + outFolder)

##Define Functions
def roundTime(dt, roundTo=60):
    seconds = (dt - dt.min).seconds
    rounding = (seconds+roundTo/2) // roundTo * roundTo
    return dt + datetime.timedelta(0,rounding-seconds,-dt.microsecond)

def selectWatershed(watershed):
    ''' Initialize all Relevant Data from the Geodatabase based on chosen watershed '''
    stations = '' # Feature class of station meta data/locations
    elev_tiff = '' # Needed for wind speed. Cannot have NoData cells
    dem = '' # Needed for almost all/elev_tiff can also be used for this
    view_factor = '' # Needed for Thermal radiation
    search_radius = ''
    db = ''

    if watershed == 'Johnston Draw':
        arcpy.AddMessage('Johnston Draw Watershed')
        base_path = r'C:\ReynoldsCreek\Input_Data'
        stations = r'{0}\Input_Data.gdb\station_locations_jd'.format(base_path)
        stations_soil = r'{0}\Input_Data.gdb\station_locations_jd'.format(base_path)
        elev_tiff = r'{0}\jd_elevation_filled.tif'.format(base_path)
        dem = elev_tiff
        view_factor = r'{0}\jd_view_factor.tif'.format(base_path)
        search_radius = '1000'
        db = '{0}/jd_data.db'.format(base_path)
        data['sql_ph'] = '?'

    elif watershed == 'Reynolds Creek':
        arcpy.AddMessage('Reynolds Creek Watershed')
        base_path = r'C:\ReynoldsCreek\Input_Data'
        stations = r'{0}\Input_Data.gdb\station_locations_rc'.format(base_path)
        stations_soil = r'{0}\Input_Data.gdb\station_locations_rc_soil'.format(base_path)
        elev_tiff = r'{0}\rc_elev_filled.tif'.format(base_path)
        dem = elev_tiff
        view_factor = r'{0}\rc_view_factor.tif'.format(base_path)
        search_radius = '10000'
        db = r'{0}\rc_data.db'.format(base_path)
        data['sql_ph'] = '?'

    elif watershed == 'Valles Caldera':
        arcpy.AddMessage('Valles Caldera Watershed')
        base_path = r'C:\ReynoldsCreek\Input_Data'
        stations = r'{0}\Input_Data.gdb\station_locations_vc'.format(base_path)
        stations_soil = r'{0}\Input_Data.gdb\station_locations_vc'.format(base_path)
        elev_tiff = r'{0}\vc_elev_filled.tif'.format(base_path)
        dem = elev_tiff
        view_factor = ''
        search_radius = '21500'
        db = r'{0}\vc_data.db'.format(base_path)
        data['sql_ph'] = '?'

    ##elif watershed == 'TESTING':
    ##    arcpy.AddMessage('Testing watershed')
    ##    file_path = os.path.dirname(os.path.abspath(__file__))
    ##    base_path = r'{0}\demo_data'.format(file_path)
    ##    stations = '{0}\demo_sites.shp'.format(base_path)
    ##    elev_tiff = '{0}\demo_data.tif'.format(base_path)
    ##    dem = '{0}\demo_data.tif'.format(base_path)
    ##    view_factor = '{0}\demo_data_vf.tif'.format(base_path)
    ##    db = '{0}\demo.db'.format(base_path)
    ##    search_radius = '1000'
    ##    data['sql_ph'] = '?'
    return stations, stations_soil, elev_tiff, dem, view_factor, search_radius, db

def ConnectDB(db, username = 'root', passwd = ''):
    '''connect to MySQL database'''
    if len(db.split('.')) == 1:
        try:
            cnx = mysql.connector.connect(user=username, password=passwd,
                                        host='localhost',
                                        database=db,
                                        buffered=True)
            return cnx
        except mysql.connector.Error as err:

            if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                arcpy.AddMessage('Something is wrong with your user name or password')
            elif err.errno == errorcode.ER_BAD_DB_ERROR:
                arcpy.AddMessage('Database does not exist')
            else:
                arcpy.AddMessage(err)
        else:
            arcpy.AddMessage('Connection successful')
    ## Connect to sqlite3 database
    elif db.split('.')[-1] == 'db':
        cnx = sqlite3.connect(db)
        return cnx

def ParameterList(param_dict, rows, table_type):
    '''Append all data to the end of the parameter list'''
    if table_type == 'climate':
        for row in rows:
            if data['watershed'] == 'Johnston Draw' or data['watershed'] == 'TESTING':
                param_dict['site_key'].append(row[0])
                param_dict['date_time'].append(row[1])
                param_dict['air_temperature'].append(row[8])
                param_dict['vapor_pressure'].append(row[10])
                param_dict['dew_point'].append(row[11])
                param_dict['solar_radiation'].append(row[12])
                param_dict['wind_speed'].append(row[13])
                param_dict['wind_direction'].append(row[14])
            elif data['watershed'] == 'Reynolds Creek' or data['watershed'] == 'Valles Caldera':
                param_dict['site_key'].append(row[0])
                param_dict['date_time'].append(row[1])
                param_dict['air_temperature'].append(row[9])
                param_dict['vapor_pressure'].append(row[11])
                param_dict['dew_point'].append(row[12])
                param_dict['solar_radiation'].append(row[13])
                param_dict['wind_speed'].append(row[14])
                param_dict['wind_direction'].append(row[15])
    elif table_type == 'precip':
        for row in rows:
            if data['watershed'] == 'Johnston Draw' or data['watershed'] == 'TESTING':
                param_dict['site_key'].append(row[0])
                param_dict['ppts'].append(row[2])
                param_dict['pptu'].append(row[3])
                param_dict['ppta'].append(row[4])
            elif data['watershed'] == 'Reynolds Creek' or data['watershed'] == 'Valles Caldera':
                param_dict['site_key'].append(row[0])
                param_dict['ppts'].append(row[2])
                param_dict['pptu'].append(row[3])
                param_dict['ppta'].append(row[4])
    elif table_type == 'soil_temperature':
        for row in rows:
            if data['watershed'] == 'Johnston Draw':
                param_dict['site_key'].append(row[0])
                param_dict['stm005'].append(row[3]) # column 3 is soil temp at 5 cm depth
            if data['watershed'] == 'Reynolds Creek':
                param_dict['site_key'].append(row[0])
                param_dict['stm005'].append(row[4]) # column 3 is soil temp at 5 cm depth
    elif table_type == 'snow_depth':
        for row in rows:
            if data['watershed'] == 'Johnston Draw' or data['watershed'] == 'Reynolds Creek' or data['watershed'] == 'TESTING':
                param_dict['site_key'].append(row[0])
                param_dict['zs'].append(row[-1])
    ##arcpy.AddMessage(param_dict)
    return param_dict

def BuildClimateTable(params, num):
    arcpy.management.CreateTable(data['scratch_gdb'], 'climate_table')
    table = data['scratch_gdb'] + '/climate_table'
    keys = [] # Holds data types collected (wind speed, air temperature, etc) to add to table
    for key in params:
        if key == 'site_key':
            ftype = 'TEXT'
        elif key == 'date_time':
            ftype = 'DATE'
        else:
            ftype = 'FLOAT'
        arcpy.management.AddField(in_table = table,
            field_name = key,
            field_type = ftype)
        keys.append(key)
    in_cursor = arcpy.InsertCursor(table)
    #print keys
    #print params
    #Add data from rows into climate table
    for j in range(0, num):
        row = in_cursor.newRow()
        for k in range(0, len(keys)):
            # keys[x] = site_key, air_temperature, etc.
            # params[keys[k][j] = value (ie -2.5)
            row.setValue(keys[k], params[ keys[k] ][j])
        in_cursor.insertRow(row)

    del in_cursor
##     del row
    return table

def DataTable(parameter, data_table, multi_fields = []):
    ''' Create paramater scratch table to be used for interpolation '''
    scratch_data = []
    temp_table1 = parameter + '_table'
    temp_table2 = 'in_memory/' + parameter + '_table2'

    ##===============================================================
    ##
    ## These checks really need some work.
    ##
    ##===============================================================
    if len(multi_fields) == 2: #Simplify these checks somehow
        #Thermal radation stats_fields
        #  format - [['air_temperature', 'MEAN'], ['vapor_pressure', 'MEAN']]
        stats_fields = []
        clause = '{0} > -500 AND {1} > -500'.format(multi_fields[0], multi_fields[1])
        for l in multi_fields:
            stats_fields.append([l, 'MEAN'])
    elif len(multi_fields) == 3:
        # Wind speed
        stats_fields = []
        clause = '{0} > -500 AND {1} > -500 AND {2} > -500'.format(multi_fields[0], multi_fields[1], multi_fields[2])
        for l in multi_fields:
            stats_fields.append([l, 'MEAN'])
    else: # regular parameters
        stats_fields = parameter + ' MEAN'
        clause = parameter + ' > -500'
    # Make new temporary table
    out = arcpy.management.MakeTableView(in_table = data_table,
            out_view = temp_table1,
            where_clause = clause)
    scratch_data.append(temp_table1)
    out_mem = arcpy.analysis.Statistics(in_table = temp_table1,
            out_table = temp_table2,
            statistics_fields = stats_fields,
            case_field = 'site_key')
    # Copy stats to tempStations feature class
    if parameter == 'stm005':
        ###====================================
        ###
        ### Soil temperature feature class already has elevation data for all feature classes
        ###
        ###====================================
        arcpy.env.extent = data['station_locations_soil']
        temp_stations = arcpy.management.CopyFeatures(in_features = data['station_locations_soil'],
                out_feature_class = data['scratch_gdb'] + '/tempStations')
    else:
        temp_stations = arcpy.management.CopyFeatures(in_features = data['fc_stations_elev'],
                out_feature_class = data['scratch_gdb'] + '/tempStations')

    # Join stats to temp stations feature class
    if len(multi_fields) > 0: #Thermal radiation and wind speed
        tr_fields = []
        for l in multi_fields:
            tr_fields.append('MEAN_' + l)
        arcpy.management.JoinField(in_data = temp_stations,
                in_field = 'Site_key',
                join_table = temp_table2,
                join_field = 'site_key',
                fields = tr_fields)
    else: # Regular paramters
        arcpy.management.JoinField(in_data = temp_stations,
            in_field = 'Site_Key',
            join_table = temp_table2,
            join_field = 'site_key',
            fields = 'MEAN_' + parameter)

    # Delete rows from feature class that have negative or null elevations

    cursor = arcpy.UpdateCursor(temp_stations)
    if parameter == 'stm005':
        arcpy.AddMessage('Soil temperature')
        arcpy.env.extent = data['station_locations_soil']

    else:
        for row in cursor:
            if (row.getValue('RASTERVALU') < 0 or
                    row.getValue('RASTERVALU') == 'None' or
                    row.getValue('RASTERVALU') is None ):
                cursor.deleteRow(row)
            else:
                row.setValue('RASTERVALU', round(row.getValue('RASTERVALU'), 2))
                cursor.updateRow(row)

        del cursor
        del row
    # Delete rows from feature class that have null values for paramter
    cursor = arcpy.UpdateCursor(temp_stations)
    if len(multi_fields) == 2: #thermal Radiation check
        for row in cursor:
            val0 = 'MEAN_' + multi_fields[0]
            val1 = 'MEAN_' + multi_fields[1]
            if row.isNull(val0) or row.isNull(val1):
                cursor.deleteRow(row)
    if len(multi_fields) == 3: # Wind speed
        for row in cursor:
            val0 = 'MEAN_' + multi_fields[0]
            val1 = 'MEAN_' + multi_fields[1]
            val2 = 'MEAN_' + multi_fields[2]
            if row.isNull(val0) or row.isNull(val1) or row.isNull(val2):
                cursor.deleteRow(row)
    else:
        for row in cursor:
            if row.isNull('MEAN_' + parameter):
                cursor.deleteRow(row)
            else:
                row.setValue('MEAN_' + parameter, round(row.getValue('MEAN_' + parameter), 2))
                cursor.updateRow(row)
    del cursor
    del row
    DeleteScratchData(scratch_data)
    return temp_stations

def DetrendedMethod(parameter, data_table, date_stamp, out_ras):
    arcpy.AddMessage('Detrended Kriging')
    resid_raster = data['scratch_gdb'] + '/' + parameter
    out_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], out_ras, date_stamp, data['file_format'])
    # Add unique ID field to temporary data table for use in OLS function
    arcpy.management.AddField(in_table = data_table,
            field_name = 'Unique_ID',
            field_type = 'SHORT',
            field_is_nullable = 'NULLABLE',
            field_is_required = 'NON_REQUIRED')
    arcpy.management.CalculateField(in_table = data_table,
            field = 'Unique_ID',
            expression = '!OBJECTID!',
            expression_type = 'PYTHON_9.3')
    #Run ordinary least squares of temporary data table
    coef_table = arcpy.management.CreateTable(data['scratch_gdb'], 'coef_table_' + parameter)
    ols = arcpy.stats.OrdinaryLeastSquares(Input_Feature_Class = data_table,
            Unique_ID_Field = 'Unique_ID',
            Output_Feature_Class = 'in_memory/fcResid',
            Dependent_Variable = 'MEAN_' + parameter,
            Explanatory_Variables = 'RASTERVALU',
            Coefficient_Output_Table = coef_table)
    intercept = list((row.getValue('Coef') for row in arcpy.SearchCursor(coef_table, fields='Coef')))[0]
    slope = list((row.getValue('Coef') for row in arcpy.SearchCursor(coef_table, fields='Coef')))[1]
    #Calculate residuals and add them to temporary data table
    arcpy.management.AddField(in_table = data_table,
            field_name = 'residual',
            field_type = 'DOUBLE',
            field_is_nullable = 'NULLABLE',
            field_is_required = 'NON_REQUIRED')
    cursor = arcpy.UpdateCursor(data_table)
    for row in cursor:
        row_math = row.getValue('MEAN_' + parameter) - ((slope * row.getValue('RASTERVALU')) + intercept)
        row.setValue('residual', row_math)
        cursor.updateRow(row)
    del cursor
    del row
    #Run ordinary kriging on residuals
    #Dewpoint/Vapor pressure kriging model
    k_model = KrigingModelOrdinary('SPHERICAL', 460, 3686, .1214, .2192)
    #Air temp kriging model
    #k_model = KrigingModelOrdinary('LINEAR', 37.061494)
    radius = RadiusFixed(10000, 1)
    outKrig = arcpy.sa.Kriging(in_point_features = data_table,
            z_field = 'residual',
            kriging_model = k_model,
            cell_size = data['output_cell_size'],
            search_radius = radius)
    outKrig.save(resid_raster)
    return_raster = arcpy.Raster(resid_raster) + (arcpy.Raster(data['dem']) * slope + intercept)
    if(data['file_format'] == 'ASC'):
        arcpy.conversion.RasterToASCII(return_raster, out_raster_name)
    else:
        return_raster.save(out_raster_name)
    #Delete scratch/residual data.
    del outKrig
    del k_model
    del radius
    arcpy.management.Delete(resid_raster)
    return out_raster_name

def IDWMethod(parameter, data_table, date_stamp, out_ras):
    arcpy.AddMessage('Inverse Distance Weighted')
    scratch_raster = '{0}/{1}'.format(data['scratch_gdb'], parameter)
    out_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], out_ras, date_stamp, data['file_format'])
    idw_out = arcpy.sa.Idw(in_point_features = data_table,
            z_field = 'MEAN_' + parameter,
            cell_size = data['elev_tiff'],
            power = 2)
    if(data['file_format'] == 'ASC'):
        arcpy.conversion.RasterToASCII(idw_out, out_raster_name)
    else:
        idw_out.save(out_raster_name)
        arcpy.AddMessage('Out Raster {0}'.format(out_raster_name))
    
    return out_raster_name
            
    
def EBKMethod(parameter, data_table, date_stamp, out_ras):
    arcpy.AddMessage('Empirical Bayesian Kriging')
    scratch_raster = '{0}/{1}'.format(data['scratch_gdb'], parameter)
    out_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], out_ras, date_stamp, data['file_format'])
    #arcpy.AddMessage(data_table)
    arcpy.ga.EmpiricalBayesianKriging(in_features = data_table,
            z_field = 'MEAN_' + parameter,
            out_raster = scratch_raster,
            cell_size = data['output_cell_size'],
            transformation_type = 'EMPIRICAL',
            max_local_points = '100',
            overlap_factor = '1',
            number_semivariograms = '100',
            search_neighborhood = 'NBRTYPE=SmoothCircular RADIUS={0} SMOOTH_FACTOR=0.2'.format(data['search_radius']),
            output_type = 'PREDICTION',
            quantile_value = '0.5',
            threshold_type = 'EXCEED',
            semivariogram_model_type='WHITTLE_DETRENDED')
    #Mask output to size of original DEM

    ## For some reason this is no longer a problem.
    ## Extract By Mask does not run well on newer versions of arcmap so it is not used.
    #outExtract = ExtractByMask(scratch_raster, data['dem'])
    outExtract = arcpy.Raster(scratch_raster)
    if(data['file_format'] =='ASC'):
        arcpy.conversion.RasterToASCII(outExtract, out_raster_name)
    else:
        outExtract.save(out_raster_name)
    arcpy.management.Delete(scratch_raster)
    return out_raster_name

def CombinedMethod(parameter, data_table, date_stamp, out_ras):
    arcpy.AddMessage('Combined Method')
    scratch_raster = '{0}/{1}'.format(data['scratch_gdb'], parameter)
    resid_raster = '{0}/{1}_{2}'.format(data['scratch_gdb'], parameter, 'residual')
    out_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], out_ras, date_stamp, data['file_format'])
    # Add unique ID field to temporary data table for use in OLS function
    arcpy.management.AddField(in_table = data_table,
            field_name = 'Unique_ID',
            field_type = 'SHORT',
            field_is_nullable = 'NULLABLE',
            field_is_required = 'NON_REQUIRED')
    arcpy.management.CalculateField(in_table = data_table,
            field = 'Unique_ID',
            expression = '!OBJECTID!',
            expression_type = 'PYTHON_9.3')
    #Run ordinary least squares of temporary data table
    coef_table = arcpy.management.CreateTable(data['scratch_gdb'], 'coef_table_' + parameter)
    ols = arcpy.stats.OrdinaryLeastSquares(Input_Feature_Class = data_table,
            Unique_ID_Field = 'Unique_ID',
            Output_Feature_Class = 'in_memory/fcResid',
            Dependent_Variable = 'MEAN_' + parameter,
            Explanatory_Variables = 'RASTERVALU',
            Coefficient_Output_Table = coef_table)
    intercept = list((row.getValue('Coef') for row in arcpy.SearchCursor(coef_table, fields='Coef')))[0]
    slope = list((row.getValue('Coef') for row in arcpy.SearchCursor(coef_table, fields='Coef')))[1]
    #Calculate residuals and add them to temporary data table
    arcpy.management.AddField(in_table = data_table,
            field_name = 'residual',
            field_type = 'DOUBLE',
            field_is_nullable = 'NULLABLE',
            field_is_required = 'NON_REQUIRED')
    cursor = arcpy.UpdateCursor(data_table)
    for row in cursor:
        row_math = row.getValue('MEAN_' + parameter) - ((slope * row.getValue('RASTERVALU')) + intercept)
        row.setValue('residual', row_math)
        cursor.updateRow(row)
    del cursor
    del row
    arcpy.ga.EmpiricalBayesianKriging(in_features = data_table,
            z_field = 'MEAN_' + parameter,
            out_raster = resid_raster,
            cell_size = data['output_cell_size'],
            transformation_type = 'EMPIRICAL',
            max_local_points = '100',
            overlap_factor = '1',
            number_semivariograms = '100',
            search_neighborhood = 'NBRTYPE=SmoothCircular RADIUS=10000.9518700025 SMOOTH_FACTOR=0.2',
            output_type = 'PREDICTION',
            quantile_value = '0.5',
            threshold_type = 'EXCEED',
            semivariogram_model_type='WHITTLE_DETRENDED')
    out_extract = arcpy.sa.ExtractByMask(resid_raster, data['dem'])
    out_extract.save(scratch_raster)

    #Add back elevation trends and save final raster
    output_raster = arcpy.Raster(scratch_raster) + (arcpy.Raster(data['dem']) * slope + intercept)
    if(data['file_format'] == 'ASC'):
        arcpy.conversion.RasterToASCII(output_raster, out_raster_name)
    else:
        output_raster.save(out_raster_name)

    arcpy.management.Delete(scratch_raster)
    arcpy.management.Delete(resid_raster)
    return out_raster_name

def Interpolate(parameter, scratch_table, date_stamp, out_name):
    '''Interpolate using the chosen method'''
    raster = ''
    if data['kriging_method'] == 'Detrended':
        raster = DetrendedMethod(parameter, scratch_table, date_stamp, out_name)
        #raster.save(data['out_folder'] + '/' + param + '.tif')
    elif data['kriging_method'] == 'Combined':
        raster = CombinedMethod(parameter, scratch_table, date_stamp, out_name)
    elif data['kriging_method'] == 'IDW':
        raster = IDWMethod(parameter, scratch_table, date_stamp, out_name)
    else:
        try:
            raster = EBKMethod(parameter, scratch_table, date_stamp, out_name)
        except arcpy.ExecuteError:
            arcpy.AddMessage(arcpy.GetMessages(2))
    return raster

def OLS(parameter, scratch_table, date_stamp, out_name):
    out_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], out_name, date_stamp, data['file_format'])
    #Run ordinary least squares on scratch_table
    coef_table = arcpy.management.CreateTable(data['scratch_gdb'], 'coef_table')
    if parameter == 'stm005':
        exp_var = 'Elevation'
    else:
        exp_var = 'RASTERVALU'
    arcpy.management.AddField(in_table = scratch_table,
            field_name = 'Unique_ID',
            field_type = 'SHORT',
            field_is_nullable = 'NULLABLE',
            field_is_required = 'NON_REQUIRED')
    arcpy.management.CalculateField(in_table = scratch_table,
        field = 'Unique_ID',
        expression = '!OBJECTID!',
        expression_type = 'PYTHON_9.3')

    ols = arcpy.stats.OrdinaryLeastSquares(Input_Feature_Class = scratch_table,
            Unique_ID_Field = 'Unique_ID',
            Output_Feature_Class = 'in_memory/fcResid',
            Dependent_Variable = 'MEAN_' + parameter,
            Explanatory_Variables = exp_var,
            Coefficient_Output_Table = coef_table)
    intercept = list((row.getValue('Coef') for row in arcpy.SearchCursor(coef_table, fields='Coef')))[0]
    slope = list((row.getValue('Coef') for row in arcpy.SearchCursor(coef_table, fields='Coef')))[1]

    arcpy.env.extent = data['ext_elev']
    return_raster = arcpy.Raster(data['dem']) * slope + intercept
    if(data['file_format'] == 'ASC'):
        arcpy.conversion.RasterToASCII(return_raster, out_raster_name)
    else:
        return_raster.save(out_raster_name)
    return out_raster_name

def AirTemperature(clim_tab, date_stamp):
    arcpy.AddMessage('Air Temperature')
    param = 'air_temperature'
    out_raster_title = 'T_a'
    scratch_table = DataTable(param, clim_tab)
    #arcpy.management.CopyRows(scratch_table, data['scratch_gdb'] + '/temp_ta')

    #Kriging
    raster = Interpolate(param, scratch_table, date_stamp, out_raster_title)

    #Delete tempStations when done.
    #arcpy.management.Delete(scratch_table)
    return raster

def DewPoint(clim_tab, date_stamp):
    arcpy.AddMessage('Dewpoint Temperature')
    param = 'dew_point'
    scratch_table = DataTable(param, clim_tab)
    out_raster_title = 'T_pp'
    #arcpy.management.CopyRows(scratch_table, data['scratch_gdb'] + '/temp_dp')

    #Kriging
    raster = Interpolate(param, scratch_table, date_stamp, out_raster_title)

    #Delete tempStations when done
    arcpy.management.Delete(scratch_table)
    return raster

def PercentSnow(dew_point, date_stamp):
    inRas = arcpy.Raster(dew_point)
    outRas = '{0}/percent_snow_{1}.{2}'.format(data['out_folder'], date_stamp, data['file_format'])
    out_snow_ras = arcpy.sa.Con(inRas < -5.0, 1.0,
                                    arcpy.sa.Con((inRas >= -5.0) & (inRas < -3.0), 1.0,
                                       arcpy.sa.Con((inRas >= -3.0) & (inRas < -1.5), 1.0,
                                           arcpy.sa.Con((inRas >= -1.5) & (inRas < -0.5), 1.0,
                                               arcpy.sa.Con((inRas >= -0.5) & (inRas < 0.0), 0.75,
                                                   arcpy.sa.Con((inRas >= 0.0) & (inRas < 0.5), 0.25,
                                                       arcpy.sa.Con(inRas >= 0.5,0.0)))))))
    if(data['file_format'] == 'ASC'):
        arcpy.conversion.RasterToASCII(out_snow_ras, outRas)
    else:
        arcpy.management.CopyRaster(in_raster = out_snow_ras,
                out_rasterdataset=outRas,
                pixel_type = '32_BIT_FLOAT')
    return outRas

def SnowDensity(dew_point, date_stamp):
    inRas = arcpy.Raster(dew_point)
    outRas = '{0}/rho_snow_{1}.{2}'.format(data['out_folder'], date_stamp, data['file_format'])
    out_snow_density = arcpy.sa.Con(inRas < -5.0, 1.0,
                                arcpy.sa.Con((inRas >= -5.0) & (inRas < -3.0), 1.0,
                                   arcpy.sa.Con((inRas >= -3.0) & (inRas < -1.5), 1.0,
                                       arcpy.sa.Con((inRas >= -1.5) & (inRas < -0.5), 1.0,
                                           arcpy.sa.Con((inRas >= -0.5) & (inRas < 0.0), 0.75,
                                               arcpy.sa.Con((inRas >= 0.0) & (inRas < 0.5), 0.25,
                                                   arcpy.sa.Con(inRas >= 0.5,0.0)))))))

    if(data['file_format'] == 'ASC'):
        arcpy.conversion.RasterToASCII(out_snow_density, outRas)
    else:
        arcpy.management.CopyRaster(in_raster = out_snow_density,
                out_rasterdataset=outRas,
                pixel_type = '32_BIT_FLOAT')
    return outRas

def VaporPressure(clim_tab, date_stamp):
    arcpy.AddMessage('Vapor Pressure')
    param = 'vapor_pressure'
    scratch_table = DataTable(param, clim_tab)
    out_raster_title = 'e_a'
    #arcpy.management.CopyRows(scratch_table, data['scratch_gdb'] + '/temp_ta')

    #Kriging
    raster = Interpolate(param, scratch_table, date_stamp, out_raster_title)

    #Delete tempStations when done.
    arcpy.management.Delete(scratch_table)
    return raster

def SolarRadiation(clim_tab, date_stamp, date_time, time_step):
    arcpy.AddMessage('Solar Radiation')
    scratch_data = []
    param = 'solar_radiation'
    out_raster_title = 'S_n'
    out_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], out_raster_title, date_stamp, data['file_format'])

    #set up area solar radiation tool parameters and run the tool
    #Set up time parameters
    day_of_year = date_time.timetuple().tm_yday
    i_sr_start = int(date_time.strftime('%H'))
    i_sr_end = i_sr_start + data['time_step']
    in_twd = TimeWithinDay(day_of_year, i_sr_start, i_sr_end)
    sky_size = 200

    try:
        out_global_radiation = arcpy.sa.AreaSolarRadiation(data['dem'], '', sky_size, in_twd)
        #out_global_radiation = out_global_radiation / data['time_step']
    except arcpy.ExecuteError:
        msgs = arcpy.GetMessages(2)
        #arcpy.AddMessage(msgs)
        if 'Failed to open raster dataset' in msgs or 'Error in creating sun map' in msgs:
            arcpy.AddMessage("Skip night hours")
            return
    #Set up scratch data table
    scratch_table = DataTable(param, clim_tab)
    scratch_data.append(scratch_table)

    glob_rad_raster = data['scratch_gdb'] + '/glob_rad_raster'
    sim_points = data['scratch_gdb'] + '/simPoints'
    scratch_data.append(glob_rad_raster)
    scratch_data.append(sim_points)
    #Correct global radiation raster for cloud conditions
    #Extract simulated global radiation values to station location feature class
    arcpy.management.AlterField(in_table = scratch_table,
            field = 'RASTERVALU',
            new_field_name = 'Elevation')
    arcpy.sa.ExtractValuesToPoints(in_point_features = scratch_table,
            in_raster = out_global_radiation,
            out_point_features = sim_points,
            interpolate_values = 'NONE',
            add_attributes = 'VALUE_ONLY')

    arcpy.management.AddField(in_table = sim_points,
            field_name = 'ratio',
            field_type = 'FLOAT',
            field_is_nullable = 'NULLABLE',
            field_is_required = 'NON_REQUIRED')
    arcpy.management.CalculateField(in_table = sim_points,
            field = 'ratio',
            expression = '!MEAN_solar_radiation!/ !RASTERVALU!',
            expression_type = 'PYTHON_9.3')
    #convert 'ration' field to numpy array
    na = arcpy.da.TableToNumPyArray(sim_points, 'ratio')

    #calculate average ratio
    d_mean_ratio = numpy.mean(na['ratio'])
    d_mean_ratio2 = numpy.asscalar(d_mean_ratio)

    #multiply simulated raster by average ratio
    out_global_radiation_corrected = out_global_radiation * d_mean_ratio2
    if(data['file_format'] == 'ASC'):
        arcpy.conversion.RasterToASCII(out_global_radiation, out_raster_name)
    else:
        out_global_radiation_corrected.save(out_raster_name)

    arcpy.management.Delete(scratch_table)
    return out_raster_name

def ThermalRadiation(clim_tab, date_stamp, in_air, in_vap, in_surface_temp):
    arcpy.AddMessage('Thermal Radiation')
    param = 'thermal_radiation'
    out_raster_title = 'I_lw'
    out_file = '{0}/{1}_{2}.{3}'.format(data['out_folder'], out_raster_title, date_stamp, data['file_format'])
    z = data['dem']
    vf = data['view_factor']
    T_a = in_air
    vp = in_vap

    fields = ['air_temperature', 'vapor_pressure']
    scratch_table = DataTable(param, clim_tab, multi_fields=fields)
    P_m = 0.0 # Reference Air Pressure (Vapor pressure)
    T_m = 0.0 # Reference Air Temp
    z_m = 0.0 # Reference elevation
    T_s = in_surface_temp
    cursor = arcpy.UpdateCursor(scratch_table)
    for row in cursor:
        z_m = row.getValue('RASTERVALU')
        P_m = row.getValue('MEAN_vapor_pressure')
        T_m = row.getValue('MEAN_air_temperature')
        cursor.deleteRow(row)
        break
    del cursor
    del row

    arcpy.AddMessage("P_m: " + str(P_m))
    arcpy.AddMessage("T_m: " + str(T_m))
    arcpy.AddMessage("z_m: " + str(z_m))
    arcpy.AddMessage("T_s: " + str(T_s))

    # Constants
    g = 9.8 # Gravity
    m = 0.0289 # Molecular Weight of dry air
    R = 8.3143 # Gas constant
    sigma = 5.6697 * 10 ** -8 # Stefan-Boltzmann constant
    epsilon_s = 0.95 # Surface emissivity
    gamma = -0.006 # temperature lapse rate (K m^-1)

    # convert temperature parameters to Kelvin
    T_m = T_m + 274.15
    T_s = T_s + 274.15
    T_a = arcpy.sa.Float(Raster(T_a) + 274.15)

    # convert vapor pressure to mb
    P_m = P_m * 0.01
    vp = arcpy.sa.Float(Raster(vp) * 0.01)

    #Correct air temperature and vapor pressure rasters (Marks and Dozier (1979), pg. 164)
    #(4) corrected air temperature
    T_prime = T_a + (0.0065 * arcpy.Raster(z))
    #saturated vapor pressure from original air temperature (T_a)
    e_sa = arcpy.sa.Float(6.11 * 10**((7.5*arcpy.sa.Float(T_a))/(237.3 + arcpy.sa.Float(T_a))))
    #saturated vapor pressure from corrected air temperature (T_prime)
    e_sprime = arcpy.sa.Float(6.11 * 10**((7.5*arcpy.sa.Float(T_a))/(237.3 + arcpy.sa.Float(T_a))))
    rh = arcpy.sa.Float(vp / e_sa) #(5) relative humidity
    e_prime = arcpy.sa.Float(rh * e_sprime) #(6) corrected vapor pressure

    #Pressure at a given elevation (Marks and Dozier (1979), pg. 168-169)
    term1 = ((-g*m)/(R*gamma))
    delta_z = arcpy.Raster(z) - z_m
    term2 = ((T_m + gamma * delta_z)) / T_m
    lnTerm = arcpy.sa.Ln(term2)
    expTerm = arcpy.sa.Exp(term1 * lnTerm)
    P_a = P_m * expTerm #(10) air pressure

    #effective emissivity (Marks and Dozier (1979), pg. 164)
    epsilon_a = arcpy.sa.Float((1.24 * (e_prime / T_prime)**(1/7)) * (P_a / 1013.0)) #(7)

    #Incoming longwave radiation (Marks and Dozier (1979), pg. 164)
    term3 = arcpy.sa.Float((epsilon_a * sigma * (T_a ** 4)) * vf)
    term4 = arcpy.sa.Float(epsilon_s * sigma * (T_s ** 4))
    term5 = (1 - arcpy.Raster(vf))
    output_thermal_radiation = arcpy.sa.Float(term3 + (term4 * term5)) #(9)
    if(data['file_format'] == 'ASC'):
        arcpy.conversion.RasterToASCII(output_thermal_radiation, out_file)
    else:
        output_thermal_radiation.save(out_file)

    return out_file

def PrecipitationMass(precip_tab, date_stamp):
    arcpy.AddMessage('Precipitation mass')
    param = 'ppta'
    out_raster_title = 'm_pp'
    out_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], out_raster_title, date_stamp, data['file_format'])
    scratch_table = DataTable(param, precip_tab)

    if data['watershed'] == 'Johnston Draw':
        cursor = arcpy.SearchCursor(scratch_table)
        x = []
        y = []
        for row in cursor:
            x.append(row.getValue('RASTERVALU'))
            y.append(row.getValue('MEAN_ppta'))
        del cursor
        del row

        A = numpy.vstack([x,numpy.ones(len(x))]).T
        slope, intercept = numpy.linalg.lstsq(A, y)[0]
        arcpy.AddMessage('Slope {0}, Intercept {1}'.format(slope, intercept))
        if slope != 0.0 and intercept != 0.0:
            #Create final raster
            arcpy.env.extent = data['ext_elev']
            raster = (arcpy.Raster(data['dem']) * slope + intercept)
            if(data['file_format'] == 'ASC'):
                arcpy.conversion.RasterToASCII(raster, out_raster_name)
            else:
                raster.save(out_raster_name)
            return out_raster_name
        else:
            return
    else:
        raster = Interpolate(param, scratch_table, date_stamp, out_raster_title)

        #Delete tempStations when done
        arcpy.management.Delete(scratch_table)
        return raster

def SoilTemperature(soil_tab, date_stamp):
    arcpy.AddMessage('Soil Temperature')
    param = 'stm005'
    out_raster_title = 'T_g'
    #Create Scratch Table --
    # this is different from the rest in that it does not delete no elevation
    scratch_table = DataTable(param, soil_tab)
    raster = OLS(param, scratch_table, date_stamp, out_raster_title)
    arcpy.management.Delete(scratch_table)
    return raster

def SnowDepth(snow_tab, date_stamp):
    arcpy.AddMessage('Snow depth')
    param = 'zs'
    out_raster_title = 'zs'
    scratch_table = DataTable(param, snow_tab)

    cursor = arcpy.SearchCursor(scratch_table)
    values = []
    for row in cursor:
        values.append(row.getValue('MEAN_zs'))
    del cursor
    del row
    average = numpy.mean(values)
    count = int(arcpy.management.GetCount(scratch_table).getOutput(0))
    if count >= 10 and average > 0:
        raster = Interpolate(param, scratch_table, date_stamp, out_raster_title)
    else:
        if count < 10:
            arcpy.AddMessage('Not enough data for snow depth. Try a different time step.')
        if average == 0:
            arcpy.AddMessage('No snow on the ground. Try a different time step if needed.')

    arcpy.management.Delete(scratch_table)
    return raster

def SnowCoverTemperature(date_stamp):
    arcpy.AddMessage('Upper Layer')
    ul_param = 'T_s_0'
    avg_param = 'T_s'
    ul_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], ul_param, date_stamp, data['file_format'])
    avg_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], avg_param, date_stamp, data['file_format'])

    if len(data['ul_interp_values']['features']) <= 1:
        upper_layer_temperature = -0.0008 * arcpy.Raster(data['dem']) + 0.1053
        if(data['file_format'] == 'ASC'):
            arcpy.conversion.RasterToASCII(upper_layer_temperature, ul_raster_name)
        else:
            upper_layer_temperature.save(ul_raster_name)
    else:
        ls_elevation = []
        ls_temperature = []
        for rec in data['ul_interp_values']['features']:
            ls_elevation.append(rec['attributes']['Elevation'])
            ls_density.append(rec['attributes']['Temperature'])
        lr_results = stats.linregress(ls_elevation, ls_density)
        slope_ul = lr_results[0]
        intercept_ul = lr_results[1]
        upper_layer_temperature = slope_ul * arcpy.Raster(data['dem']) + intercept_ul
        if(data['file_format'] == 'ASC'):
            arcpy.conversion.RasterToASCII(upper_layer_temperature, ul_raster_name)
        else:
            upper_layer_temperature.save(ul_raster_name)
    if len(data['ll_interp_values']['features']) <=1:
        lower_layer_temperature = -0.0008 * arcpy.Raster(data['dem']) + 1.3056
    else:
        ls_elevation = []
        ls_temperature = []
        for rec in data['ll_interp_values']['features']:
            ls_elevation.append(rec['attributes']['Elevation'])
            ls_temperature.append(rec['attributes']['Temperature'])

        lr_results = stats.linregress(ls_elevation, ls_temperature)
        slope_ll = lr_results[0]
        intercept_ll = lr_results[1]
        lower_layer_temperature = slope_ll * arcpy.Raster(data['dem']) + intercept_ll

    #average snowcover temperature is the average of the upper and lower layer temperatures
    avg_sc_temp = arcpy.sa.CellStatistics([upper_layer_temperature, lower_layer_temperature], 'MEAN', 'NODATA')
    if data['file_format'] == 'ASC':
        arcpy.conversion.RasterToASCII(avg_sc_temp, avg_raster_name)
    else:
        avg_sc_temp.save(avg_raster_name)
    return ul_raster_name, avg_raster_name

def SnowDensityInterpolation(date_stamp):
    arcpy.AddMessage('Snow Density Interpolation')
    param = 'rho'
    out_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'], param, date_stamp, data['file_format'])
    if len(data['density_interp_values']['features']) <= 1:
        snow_density_raster = -0.0395 * arcpy.Raster(data['dem']) + 405.26
        if data['file_format'] == 'ASC':
            arcpy.conversion.RasterToASCII(snow_density_raster, out_raster_name)
        else:
            snow_density_raster.save(out_raster_name)

    else: # This will not work until we get scypy loaded
        ls_elevation = []
        ls_density = []
        for rec in data['density_interp_values']['features']:
            ls_elevation.append(rec['attributes']['Elevation'])
            ls_density.append(rec['attributes']['Density'])
        lr_results = stats.linregress(ls_elevation, ls_density)
        slope = lr_results[0]
        intercept = lr_results[1]
        snow_density_raster = slope * arcpy.Raster(data['dem']) + intercept
        snow_density_raster.save(out_raster_name)
    return out_raster_name

def Constants(rl, h2o, date_stamp):
    arcpy.AddMessage('Constants')
    rl_param = 'z_0'
    h2o_param = 'h2o_sat'
    rl_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'],rl_param,date_stamp, data['file_format'])
    h2o_raster_name = '{0}/{1}_{2}.{3}'.format(data['out_folder'],h2o_param,date_stamp, data['file_format'])
    desc = arcpy.Describe(data['dem'])
    coord_system = desc.spatialReference
    rl_constant = CreateConstantRaster(rl, 'FLOAT', data['output_cell_size'])
    arcpy.management.DefineProjection(rl_constant, coord_system)
    if data['file_format'] == 'ASC':
        arcpy.conversion.RasterToASCII(rl_constant, rl_raster_name)
    else:
        rl_constant.save(rl_raster_name)
    h2o_constant = CreateConstantRaster(h2o, 'FLOAT', data['output_cell_size'])
    arcpy.management.DefineProjection(h2o_constant, coord_system)
    if data['file_format'] == 'ASC':
        arcpy.conversion.RasterToASCII(h2o_constant, h2o_raster_name)
    else:
        h2o_constant.save(h2o_raster_name)

    return rl_raster_name, h2o_raster_name

def WindSpeed(clim_tab, date_stamp, in_date_time):
    arcpy.AddMessage('Wind Speed')
    scratch_data = []
    param = 'wind_speed'
    out_raster_title = 'u'
    out_file = '{0}/{1}_{2}.{3}'.format(data['out_folder'], out_raster_title, date_stamp, data['file_format'])

    fields = ['wind_speed', 'wind_direction', 'air_temperature']
    scratch_table = DataTable(param, clim_tab, multi_fields=fields)
    ninja_path = 'Upload text'
    #ninja_path = 'C:/WindNinja/WindNinja-3.1.1/bin/WindNinja_cli.exe' # comment to upload
    wind_date = in_date_time.split(" ")[0]
    wind_time = in_date_time.split(" ")[1]
    ls_wind_date = wind_date.split("-")
    ls_wind_time = wind_time.split(":")
    wind_year = ls_wind_date[0]
    wind_month = ls_wind_date[1]
    wind_day = ls_wind_date[2]
    wind_hour = ls_wind_time[0]
    wind_minute = ls_wind_time[1]

    #Build station csv file from SQL data
    # Add coordinates to station feature class
    arcpy.management.AddGeometryAttributes(scratch_table, 'POINT_X_Y_Z_M')
    #Loop through stations in station feature class and write parameter values to a csv file
    csv_filename = data['scratch_ws'] + '/wn_stations.csv'
    with open(csv_filename, 'wb') as csvFile:
        a = csv.writer(csvFile)
        a.writerow(['Station_Name', 'Coord_Sys(PROJCS,GEOGCS)', 'Datum(WGS84,NAD83,NAD27)',
            'Lat/YCoord', 'Lon/XCoord', 'Height', 'Height_Units(meters,feet)', 'Speed',
            'Speed_Units(mph,kph,mps)', 'Direction(degrees)', 'Temperature',
            'Temperature_Units(F,C)', 'Cloud_Cover(%)', 'Radius_of_Influence',
            'Radius_of_Influence_Units(miles,feet,meters,km)'])
        cursor = arcpy.SearchCursor(scratch_table)
        for row in cursor:
            a.writerow([row.getValue("Site_Key"), 'PROJCS', 'NAD83', row.getValue("Point_Y"),
                row.getValue("Point_X"), '3', 'meters', row.getValue("MEAN_wind_speed"),
                'mps', row.getValue("MEAN_wind_direction"), row.getValue("MEAN_air_temperature"),
                'C', '0', '-1', 'miles'])
    csvFile.close()
    #List arguments for WindNinja CLI
    args = []

    # Comment forme here to end of Args for upload
    # args = [ninja_path,
    # "--initialization_method", "pointInitialization",
    # "--elevation_file", data['elev_tiff'], #elevation raster (cannot contain any "no-data" values)
    # "--match_points", "false", #match simulations to points (simulation fails if set to true)
    # "--year", wind_year,
    # "--month", wind_month,
    # "--day", wind_day,
    # "--hour", wind_hour,
    # "--minute", wind_minute,
    # "--mesh_resolution", data['output_cell_size'], #Resolution of model calculations
    # "--vegetation", "brush", #Vegetation type (can be 'grass', 'brush', or 'trees')
    # "--time_zone", "America/Boise", #time zone of target simulation
    # "--diurnal_winds", "true", #consider diurnal cycles in calculations
    # "--write_goog_output", "false", #write kml output (boolean: true/false)
    # "--write_shapefile_output", "false", #write shapefile output (boolean: true/false)
    # "--write_farsite_atm", "false", #write fire behavior file (boolean: true/false)
    # "--write_ascii_output", "true", #write ascii file output (this should always be set to true)
    # "--ascii_out_resolution", "-1", #resolution of output (-1 means same as mesh_resolution)
    # "--units_ascii_out_resolution", "m",
    # "--units_mesh_resolution", "m", #units of resolution of model calculations (should be "m" for meters)
    # "--units_output_wind_height", "m", #units of output wind height
    # "--output_speed_units", "mps",
    # "--output_wind_height", "3",
    # "--wx_station_filename", csv_filename, #weather station csv file used in point initialization method
    # "--output_path", data['scratch_ws']] #path to output

    # Last line uncomment for upload
    
    #run the WindNinja_cli.exe (output is written to the same location as elevatoin raster)
    arcpy.AddMessage('Calling WindNinja command line interface')
    runfile = subprocess.Popen(args, stdout = subprocess.PIPE, bufsize = -1)
    runfile.wait()
    output = runfile.stdout.read()
    if output is None:
        arcpy.AddMessage('Results: None returned\n')
    else:
        arcpy.AddMessage('Results:\n' + output)
    #convert ascii file to new grid
    for file in os.listdir(data['scratch_ws']):
        if file.endswith('_vel.asc'):
            path_2_ascii = '{0}/{1}'.format(data['scratch_ws'], file)
            scratch_data.append(path_2_ascii)
        elif ( file.endswith("_vel.prj") or file.endswith('_ang.asc') or
               file.endswith('_ang.prj') or file.endswith('cld.asc') or
               file.endswith('_cld.prj') ):
            scratch_data.append(data['scratch_ws'] + '/' + file)
    # if desired file format is ASC only copy to output folder
    if(data['file_format'] == 'ASC'):
        shutil.copyfile(path_2_ascii, out_file)
    else:
        arcpy.conversion.ASCIIToRaster(in_ascii_file=path_2_ascii,
            out_raster=out_file,
            data_type='FLOAT')
    #Get coordinate system information
    desc = arcpy.Describe(data['dem'])
    coord_system = desc.spatialReference
    arcpy.management.DefineProjection(out_file, coord_system)

    DeleteScratchData(scratch_data)


    return out_file
def ClearBadZeros():
    fix_zero = []
    for f in glob.glob('m_pp_*.{0}'.format(data['file_format'])):
        fix_zero.append(f)
    for f in glob.glob('zs_*.{0}'.format(data['file_format'])):
        fix_zero.append(f)
    for f in fix_zero:
        nm = f.split('.')
        raster = arcpy.Raster(f)
        zero = 0
        out_con = arcpy.sa.Con(raster, zero, raster, "VALUE < 0")
        if nm[1].lower() == 'asc':
            arcpy.management.Delete(raster)
            arcpy.conversion.RasterToASCII(out_con, '{0}\\{1}.asc'.format(data['scratch_ws'], nm[0]))
        else:
            out_con.save('{0}\\{1}.tif'.format(data['scratch_ws'], nm[0]))

def emailer(email, subject, message):
    from_addr = 'isu.wcwave@gmail.com'
    to_addrs = email
    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = to_addrs
    msg['Subject'] = subject
    message = message
    msg.attach(MIMEText(message))
    if len(to_addrs) > 2:
        username = 'isu.wcwave@gmail.com'
        password = ''
        dir_path = os.path.dirname(os.path.realpath(__file__))
        text_file = dir_path + "/password.txt"
        with open(text_file, 'r') as myfile:
            password = myfile.read() ## MAKE SURE NOT TO COMMIT THE PASSWORD TO GIT

        server = smtplib.SMTP_SSL("smtp.gmail.com:465")
        server.login(username,password)
        server.sendmail(from_addr, to_addrs, msg.as_string())
        server.quit()

def DeleteScratchData(in_list):
    #pass
    #arcpy.AddMessage("Deleting scratch data")
    for path in in_list:
        print path
        arcpy.management.Delete(path)

# Main Function --- Figure out a way to be run as script or as tool
#======================================================================
def main():
    from_date_round = datetime.datetime.strptime(data['from_date'], '%Y-%m-%d %H:%M:%S')
    to_date_round = datetime.datetime.strptime(data['to_date'], '%Y-%m-%d %H:%M:%S')
    data['from_date'] = roundTime(from_date_round, 60*60)

    data['to_date'] = roundTime(to_date_round)
    return_ws = selectWatershed(data['watershed'])
    data.update({'stations' : return_ws[0],
        'stations_soil' : return_ws[1],
        'elev_tiff' : return_ws[2],
        'dem' : return_ws[3],
        'view_factor' : return_ws[4],
        'search_radius' : return_ws[5],
        'db' : return_ws[6],
        })

    # Connect to database
    db_cnx = ConnectDB(data['db'])

    # Scratch and output lists
    ls_scratch_data = []
    ls_output = []

    #Master stations feature class to be copied for each gridding function
    data.update({'fc_stations_elev': data['scratch_gdb'] + '/stations_wElev'})
    ls_scratch_data.append(data['fc_stations_elev'])
    data.update({'station_locations' : data['scratch_gdb'] + '/station_locations'})
    data.update({'station_locations_soil' : data['scratch_gdb'] + '/station_locations_soil'})

    arcpy.management.CopyFeatures(data['stations'], data['station_locations'])
    arcpy.management.CopyFeatures(data['stations_soil'], data['station_locations_soil'])

    ls_scratch_data.append(data['station_locations'])
    ls_scratch_data.append(data['station_locations_soil'])
    data['ext_features'] = arcpy.Describe(data['station_locations']).extent

    arcpy.env.cellSize = data['dem']
    arcpy.AddMessage(arcpy.Describe(data['dem']).extent)
    data.update({'output_cell_size' : arcpy.env.cellSize,
        'ext_elev' : arcpy.Describe(data['dem']).extent
        })
    arcpy.env.extent = data['ext_elev']

    arcpy.sa.ExtractValuesToPoints(in_point_features = data['station_locations'],
            in_raster = data['dem'],
            out_point_features = data['fc_stations_elev'],
            interpolate_values = 'NONE',
            add_attributes = 'VALUE_ONLY')

    delta = datetime.timedelta(hours=data['time_step'])
    date_increment = data['from_date']

    # date_counter - counter to help setup data for ISNOBAL (saved in date_file.txt)
    date_counter = 0
    date_file = open('{0}/date_file.txt'.format(data['out_folder']), 'a')
    while date_increment < data['to_date']:
        arcpy.AddMessage(' ')
        arcpy.AddMessage('Current time step: {0}'.format(date_increment))
        if any([data['bool_all_tools'], data['bool_air_temperature'],
            data['bool_dew_point'], data['bool_vapor_pressure'],
            data['bool_wind_speed'], data['bool_solar_radiation'],
            data['bool_thermal_radiation']]):
            # Run climate data
            ls_scratch_data_imd = []

            # Paramter lists
            parameters = {'site_key' : [],
                    'date_time' : [],
                    'air_temperature' : [],
                    'vapor_pressure' : [],
                    'dew_point' : [],
                    'solar_radiation' : [],
                    'wind_speed' : [],
                    'wind_direction' : []
                    }

            # Query climage (weather) table
            from_date = date_increment.strftime('%Y-%m-%d %H:%M:%S')
            time_stamp = date_increment.strftime('%Y%m%d_%H')
            to_date_temp = date_increment + delta
            to_date = to_date_temp.strftime('%Y-%m-%d %H:%M:%S')
            query = ('SELECT * FROM weather WHERE '\
                'date_time >= ' + data['sql_ph'] + ' '\
                'AND date_time < ' + data['sql_ph'] + ';')
            cur = db_cnx.cursor()
            cur.execute(query, (from_date,to_date))
            rows = cur.fetchall()
            i_num_return = len(rows)
            ##arcpy.AddMessage('Query: ' + query)
            #arcpy.AddMessage('Row Count: {0}'.format(i_num_return))
            #Build parameter lists into dictionary
            parameters = ParameterList(parameters, rows, table_type = 'climate')
            cur.close()

            # Build Climate table
            climate_table = BuildClimateTable(parameters, i_num_return)
            ls_scratch_data_imd.append(climate_table)
            # Run interpolation tools
            if data['bool_air_temperature']:
                path_air_temp = AirTemperature(climate_table, time_stamp)
                ls_output.append(path_air_temp)
            if data['bool_dew_point']:
                path_dew_point = DewPoint(climate_table, time_stamp)
                path_percent_snow = PercentSnow(path_dew_point, time_stamp)
                path_snow_density = SnowDensity(path_dew_point, time_stamp)
                ls_output.extend([path_dew_point, path_percent_snow, path_snow_density])
            if data['bool_vapor_pressure']:
                path_vapor_pressure = VaporPressure(climate_table, time_stamp)
                ls_output.append(path_vapor_pressure)
            if data['bool_wind_speed']:
                path_wind_speed = WindSpeed(climate_table, time_stamp, from_date)
                ls_output.append(path_wind_speed)
            if data['bool_solar_radiation']:
                path_solar_radiation = SolarRadiation(climate_table,
                            time_stamp,
                            date_increment,
                            data['time_step'])
                ls_output.append(path_solar_radiation)
            if data['bool_thermal_radiation']:
                #Query database for average air temperature for current day
                sFromTR = date_increment.strftime("%Y-%m-%d")
                sQuery2 = ("SELECT AVG(NULLIF(ta , -999)) FROM weather "
                           "WHERE date_time >= '" + sFromTR + " 00:00:00" + "' "
                           "AND date_time <= '" + sFromTR + " 23:00:00'")
                cur2 = db_cnx.cursor()
                cur2.execute(sQuery2)
                d_ref_temp = cur2.fetchone()[0]
                cur2.close()

                path_thermal_radiation = ThermalRadiation(climate_table,
                        time_stamp,
                        path_air_temp,
                        path_vapor_pressure,
                        d_ref_temp)
                ls_output.append(path_thermal_radiation)

            DeleteScratchData(ls_scratch_data_imd)
            arcpy.management.Delete('in_memory')
        if any([data['bool_all_tools'], data['bool_precip_mass']]):
            # Run climate data
            ls_scratch_data_imd = []

            # Initiate parameter lists
            parameters = {'site_key' : [],
                    'ppts' : [],
                    'pptu' : [],
                    'ppta' : []}

            # Query precip table
            from_date = date_increment.strftime('%Y-%m-%d %H:%M:%S')
            time_stamp = date_increment.strftime('%Y%m%d_%H')
            to_date_temp = date_increment + delta
            to_date = to_date_temp.strftime('%Y-%m-%d %H:%M:%S')
            query = ('SELECT * FROM precipitation WHERE '\
                    'date_time >= ' + data['sql_ph'] + ' '\
                    'AND date_time < ' + data['sql_ph'] + ';')
            cur = db_cnx.cursor()
            cur.execute(query, (from_date, to_date))
            rows = cur.fetchall()
            i_num_return = len(rows)
            ##arcpy.AddMessage('Query: ' + query)
            ##arcpy.AddMessage('Row Count: {0}'.format(i_num_return))
            parameters = ParameterList(parameters, rows, table_type = 'precip')
            cur.close()

            precip_table = BuildClimateTable(parameters, i_num_return)
            ls_scratch_data_imd.append(precip_table)

            if data['bool_precip_mass']:
                path_precip_mass = PrecipitationMass(precip_table, time_stamp)
                ls_output.append(path_precip_mass)
            DeleteScratchData(ls_scratch_data_imd)
            arcpy.management.Delete('in_memory')
        if any([data['bool_all_tools'], data['bool_soil_temperature']]):
            ls_scratch_data_imd = []

            parameters = {'site_key': [],
                    'stm005': []}

            #Query soil temperature table
            # Query precip table
            from_date = date_increment.strftime('%Y-%m-%d %H:%M:%S')
            time_stamp = date_increment.strftime('%Y%m%d_%H')
            to_date_temp = date_increment + delta
            to_date = to_date_temp.strftime('%Y-%m-%d %H:%M:%S')
            query = ('SELECT * FROM soil_temperature WHERE '\
                    'date_time >= ' + data['sql_ph'] + ' '\
                    'AND date_time < ' + data['sql_ph'] + ';')
            cur = db_cnx.cursor()
            cur.execute(query, (from_date, to_date))
            rows = cur.fetchall()
            i_num_return = len(rows)
            ##arcpy.AddMessage('Query: ' + query)
            ##arcpy.AddMessage('Row Count: {0}'.format(i_num_return))
            parameters = ParameterList(parameters, rows, table_type = 'soil_temperature')
            cur.close()

            soil_table = BuildClimateTable(parameters, i_num_return)
            ls_scratch_data_imd.append(soil_table)

            if data['bool_soil_temperature']:
                path_soil_temp = SoilTemperature(soil_table, time_stamp)
                ls_output.append(path_soil_temp)
            DeleteScratchData(ls_scratch_data_imd)
            arcpy.management.Delete('in_memory')
        time_stamp = date_increment.strftime('%Y%m%d_%H')
        date_file.write('{0}\t{1}\n'.format(date_counter, time_stamp))
        date_counter += 1
        date_increment += delta

    #Run initial condition functions once
    from_date = date_increment.strftime('%Y-%m-%d %H:%M:%S')
    time_stamp = date_increment.strftime('%Y%m%d_%H')
    to_date_temp = date_increment + delta
    to_date = to_date_temp.strftime('%Y-%m-%d %H:%M:%S')

    if any([data['bool_all_tools'], data['bool_snow_depth']]):
        ls_scratch_data_imd = []
        #Initiate parameter dict
        parameters = {'site_key': [],
                'zs': []
                }
        query = ('SELECT * FROM snow_depth WHERE '\
                'date_time >= ' + data['sql_ph'] + ' '\
                'AND date_time < ' + data['sql_ph'] + ';')
        cur = db_cnx.cursor()
        cur.execute(query, (from_date, to_date))
        rows = cur.fetchall()
        i_num_return = len(rows)
        ##arcpy.AddMessage('Query: ' + query)
        ##arcpy.AddMessage('Row Count: {0}'.format(i_num_return))
        #Build parameter lists into dictionary
        parameters = ParameterList(parameters, rows, table_type = 'snow_depth')
        cur.close()
        #Build Climate table
        snow_table = BuildClimateTable(parameters, i_num_return)
        ls_scratch_data_imd.append(snow_table)
        #Run gridding function
        if data['bool_snow_depth']:
            path_snow_depth = SnowDepth(snow_table, time_stamp)
            ls_output.append(path_snow_depth)
        DeleteScratchData(ls_scratch_data_imd)
        arcpy.management.Delete('in_memory')
    if data['bool_snow_properties']:
        arcpy.AddMessage('snow Properties')
        path_ul_snow_temperature, path_avg_snow_temperature = SnowCoverTemperature(time_stamp)
        path_snow_density = SnowDensityInterpolation(time_stamp)
        ls_output.extend([path_ul_snow_temperature, path_avg_snow_temperature, path_snow_density])
    if data['bool_constants']:
        path_rl_constant, path_h2o_constant = Constants(data['rl_constant'], data['h2o_constant'], time_stamp)
        ls_output.extend([path_rl_constant, path_h2o_constant])

    db_cnx.close()
    date_file.close()

    ls_scratch_data.append(scratchGDB)
    DeleteScratchData(ls_scratch_data)
    arcpy.management.Delete('in_memory')

    ClearBadZeros() ## Snow depth and precipitation update any values below zero to zero

    shutil.make_archive(data['out_folder'],'zip', data['out_folder'])

    arcpy.SetParameterAsText(22, data['out_folder'] + '.zip')

if __name__ == '__main__':
    #Dictionary to hold all user input data.
    data.update({'watershed' : arcpy.GetParameterAsText(0),
        'file_format' : arcpy.GetParameterAsText(1),
        'from_date' : arcpy.GetParameterAsText(2),
        'to_date' : arcpy.GetParameterAsText(3),
        'time_step' : int(arcpy.GetParameterAsText(4)),
        'kriging_method' : arcpy.GetParameterAsText(5),
        'bool_all_tools' : arcpy.GetParameter(6),
        'bool_air_temperature' : arcpy.GetParameter(7),
        'bool_constants' : arcpy.GetParameter(8),
        'rl_constant' : arcpy.GetParameter(9),
        'h2o_constant' : arcpy.GetParameter(10),
        'bool_dew_point' : arcpy.GetParameter(11),
        'bool_precip_mass' : arcpy.GetParameter(12),
        'bool_snow_depth' : arcpy.GetParameter(13),
        'bool_snow_properties' : arcpy.GetParameter(14),
        'll_interp_values' : json.loads(arcpy.GetParameter(15).JSON),
        'ul_interp_values' : json.loads(arcpy.GetParameter(16).JSON),
        'density_interp_values' : json.loads(arcpy.GetParameter(17).JSON),
        'bool_soil_temperature' : arcpy.GetParameter(18),
        'bool_solar_radiation' : arcpy.GetParameter(19),
        'bool_thermal_radiation' : arcpy.GetParameter(20),
        'bool_vapor_pressure' : arcpy.GetParameter(21),
        'bool_wind_speed' : arcpy.GetParameter(22),
        'email_address' : arcpy.GetParameterAsText(24),

    })

    # main()
    try:
        main()
    except:
        arcpy.AddMessage("Error")
        subject = "[VWCSIT] There was an error"
        message = arcpy.GetMessages(0)
        arcpy.AddError(message)
        emailer(data['email_address'], subject, message)
    else:
        subject = "[VWCSIT] Processing Complete"
        message = "Download the output at <>\n\n"
        message += arcpy.GetMessages(0)
        emailer(data['email_address'], subject, message) 
##     import cProfile
##     import pstats
##     pr = cProfile.Profile()
##     pr.enable()
##     main()
##     pr.disable()
##     ps = pstats.Stats(pr).sort_stats('cumulative')
##     ps.print_stats(25)
